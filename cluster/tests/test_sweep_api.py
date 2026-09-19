"""S07 sweep API tests use temporary storage and an injected fake runtime."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.templating import Jinja2Templates

from cluster.application.sweep_models import preview_catalog_sweep
from cluster.application.sweep_runner import SweepRepository, SweepSupervisor
from cluster.dashboard.schemas import (
    SweepLifecyclePayload,
    SweepReasonPayload,
    SweepSaveDraftPayload,
)
from cluster.dashboard.service_layers.errors import DashboardServiceError
from cluster.dashboard.service_layers.sweep_service import SweepDraftRepository, SweepService
from cluster.domain.sweep import SweepSpec
from cluster.infrastructure.storage import FilesystemRunRepository
from cluster.tests.test_sweep_models import TEXT, fixtures


def tearDownModule():
    # This module imports the compatibility facade for resolver tests. Stop its
    # registry watcher before later tests reload the facade with temporary paths.
    services = sys.modules.get("cluster.dashboard.services")
    if services is not None:
        services.experiments.shutdown()


class FakeBackend:
    def __init__(self) -> None:
        self.jobs = {}
        self.starts = []
        self.cancels = []

    def start(self, _plan, trial, attempt):
        job_id = attempt["backend_job_id"]
        self.starts.append(job_id)
        self.jobs.setdefault(job_id, {
            "status": "running", "backend_job_id": job_id, "run_id": None,
            "cleanup_status": "held", "finished_at": None,
        })
        return dict(self.jobs[job_id])

    def inspect(self, attempt):
        job_id = attempt["backend_job_id"]
        if job_id not in self.jobs:
            raise FileNotFoundError(job_id)
        return dict(self.jobs[job_id])

    def cancel(self, attempt):
        job_id = attempt["backend_job_id"]
        self.cancels.append(job_id)
        self.jobs[job_id].update(status="cancelled", cleanup_status="released")
        return dict(self.jobs[job_id])

    def finish(self, job_id, status="completed"):
        self.jobs[job_id].update(
            status=status, cleanup_status="released", run_id="run_" + job_id[-8:],
            finished_at="fake-finished",
            failure_code="SYNTHETIC_FAILURE" if status == "failed" else None,
        )


class FixtureResolver:
    def __init__(self) -> None:
        self.calls = []
        self.drift = False

    def __call__(self, request, refresh):
        self.calls.append(refresh)
        data = fixtures()
        raw = json.loads(json.dumps(request["spec"]))
        if self.drift and refresh:
            raw["base"]["max_tokens"] = int(raw["base"].get("max_tokens", 16)) + 1
        data["spec"] = SweepSpec.from_dict(raw)
        data["selections"] = dict(request["model_selections"])
        return preview_catalog_sweep(**data)


def request_data(*, persist_prompt=True, response_storage_mode="full"):
    data = fixtures()
    raw = data["spec"].to_dict()
    raw["base"]["persist_prompt"] = persist_prompt
    raw["base"]["response_storage_mode"] = response_storage_mode
    return {
        "spec": raw,
        "model_selections": dict(data["selections"]),
        "prompts": [{"ref": "same", "text": TEXT}],
    }


class SweepServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runs = SweepRepository(self.root / "runs")
        self.drafts = SweepDraftRepository(self.root / "drafts")
        self.resolver = FixtureResolver()
        self.backend = FakeBackend()
        self.service = SweepService(
            drafts=self.drafts,
            runs=self.runs,
            resolver=self.resolver,
            supervisor_factory=lambda prompt, drift: SweepSupervisor(
                self.runs, self.backend, drift
            ),
            run_repository=FilesystemRunRepository(self.root / "results"),
        )

    def save(self, sweep_id="sweep_api", **privacy):
        return self.service.save(SweepSaveDraftPayload(
            sweep_id=sweep_id, **request_data(**privacy)
        ))

    @staticmethod
    def lifecycle(saved, key="request-key-001"):
        return SweepLifecyclePayload(
            idempotency_key=key,
            plan_revision=saved["plan_revision"],
            plan_sha256=saved["plan_sha256"],
        )

    def test_preview_save_start_duplicate_and_server_fresh_preflight(self):
        payload = SweepSaveDraftPayload(sweep_id="sweep_api", **request_data())
        preview = self.service.preview(payload)
        self.assertEqual(preview["evidence_mode"], "cached")
        self.assertNotIn(TEXT, json.dumps(preview))
        saved = self.service.save(payload)
        started = self.service.start("sweep_api", self.lifecycle(saved))
        self.assertEqual(started["sweep"]["status"], "running")
        self.assertEqual(self.resolver.calls, [False, False, True])
        replay = self.service.start("sweep_api", self.lifecycle(saved))
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(len(self.backend.starts), 1)

    def test_stale_revision_hash_tamper_and_idempotency_conflict(self):
        saved = self.save()
        with self.assertRaisesRegex(DashboardServiceError, "revision"):
            self.service.start("sweep_api", SweepLifecyclePayload(
                idempotency_key="stale-revision", plan_revision=2,
                plan_sha256=saved["plan_sha256"],
            ))
        with self.assertRaisesRegex(DashboardServiceError, "hash"):
            self.service.start("sweep_api", SweepLifecyclePayload(
                idempotency_key="stale-hash-key", plan_revision=1,
                plan_sha256="0" * 64,
            ))
        changed = self.drafts.read("sweep_api")
        changed["plan"]["executable"] = False
        self.drafts.update("sweep_api", lambda value: value.update(plan=changed["plan"]))
        with self.assertRaisesRegex(DashboardServiceError, "integrity"):
            self.service.start("sweep_api", self.lifecycle(saved, "tamper-key"))

        saved2 = self.save("sweep_idem")
        first = self.lifecycle(saved2, "same-idempotency")
        self.service.start("sweep_idem", first)
        with self.assertRaisesRegex(DashboardServiceError, "another payload"):
            self.service.start("sweep_idem", first.model_copy(update={"plan_sha256": "1" * 64}))

    def test_fresh_drift_blocks_start_without_child(self):
        saved = self.save()
        self.resolver.drift = True
        with self.assertRaisesRegex(DashboardServiceError, "stale"):
            self.service.start("sweep_api", self.lifecycle(saved))
        self.assertEqual(self.backend.starts, [])

    def test_duplicate_start_race_uses_one_durable_child(self):
        saved = self.save("sweep_race")
        payload = self.lifecycle(saved, "race-start-key")
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(
                lambda _: self.service.start("sweep_race", payload), range(2)
            ))
        self.assertEqual(len(results), 2)
        self.assertEqual(len(self.backend.starts), 1)
        self.assertTrue(any(item["idempotent_replay"] for item in results))

    def test_pause_resume_cancel_target_and_retry_reason(self):
        saved = self.save("sweep_control")
        started = self.service.start("sweep_control", self.lifecycle(saved, "start-control"))
        reason = lambda key, text: SweepReasonPayload(
            **self.lifecycle(saved, key).model_dump(), reason=text
        )
        paused = self.service.pause(
            "sweep_control", reason("pause-key-01", "operator pause")
        )
        self.assertEqual(paused["sweep"]["phase"], "pausing")
        active_job = started["sweep"]["trials"][0]["attempts"][0]["backend_job_id"]
        self.backend.finish(active_job)
        value = self.service.get("sweep_control")["sweep"]
        self.assertEqual(value["status"], "paused")
        resumed = self.service.resume(
            "sweep_control", self.lifecycle(saved, "resume-key-1")
        )
        self.assertEqual(resumed["sweep"]["status"], "ready")
        value = self.service.get("sweep_control")["sweep"]
        second_job = value["trials"][1]["attempts"][0]["backend_job_id"]
        self.service.cancel(
            "sweep_control", reason("cancel-key-1", "operator cancel")
        )
        self.assertEqual(self.backend.cancels, [second_job])
        cancelled = self.service.get("sweep_control")["sweep"]
        self.assertEqual(cancelled["status"], "cancelled")
        retried = self.service.retry(
            "sweep_control", value["trials"][1]["trial_id"],
            reason("retry-key-01", "manual evidence cleared"),
        )
        self.assertEqual(retried["sweep"]["trials"][1]["status"], "pending")
        with self.assertRaises(DashboardServiceError):
            self.service.cancel("another-sweep", reason("other-cancel", "wrong owner"))

    def test_event_paging_replay_results_export_and_privacy_scrub(self):
        for mode in ("hash_only", "none"):
            with self.subTest(mode=mode):
                sweep_id = "privacy_" + mode
                saved = self.save(
                    sweep_id, persist_prompt=False, response_storage_mode=mode
                )
                self.service.start(sweep_id, self.lifecycle(saved, "start-" + mode))
                events = self.service.events(sweep_id, cursor=0, limit=1)
                self.assertEqual(len(events["events"]), 1)
                with self.assertRaisesRegex(DashboardServiceError, "EVENT_CURSOR_AHEAD"):
                    self.service.events(sweep_id, cursor=10_000, limit=1)
                replay = next(self.service.event_stream(sweep_id, cursor=1))
                self.assertNotIn("id: 1\n", replay)
                current = self.service.get(sweep_id)["sweep"]
                while current["status"] not in {"completed", "partial", "failed", "cancelled"}:
                    for trial in current["trials"]:
                        if trial["status"] == "running":
                            self.backend.finish(trial["attempts"][-1]["backend_job_id"])
                    current = self.service.get(sweep_id)["sweep"]
                self.assertFalse(self.drafts._prompt_path(sweep_id).exists())
                exported = self.service.export_plan(sweep_id)
                results = self.service.results(sweep_id)
                all_public = json.dumps([exported, results, self.service.events(sweep_id)])
                self.assertNotIn(TEXT, all_public)
                self.assertTrue(all(item["run_id"] for item in results["trials"][0]["attempts"]))

    def test_path_and_page_bounds_are_rejected(self):
        with self.assertRaises(DashboardServiceError):
            self.drafts.read("../escape")
        with self.assertRaises(DashboardServiceError):
            self.service.list(offset=-1)
        with self.assertRaises(DashboardServiceError):
            self.service.events("missing", cursor=-1)


class ProductionResolverTests(unittest.TestCase):
    def test_cached_preview_uses_server_catalog_inventory_and_worker_identity(self):
        from cluster.dashboard import services as dashboard_services
        data = fixtures()
        request = request_data()
        with mock.patch.object(
            dashboard_services, "_cached_sweep_inventories", return_value=data["inventories"]
        ) as cached, mock.patch.object(
            dashboard_services, "_sweep_workers", return_value=data["workers"]
        ), mock.patch.object(
            dashboard_services, "read_model_catalog", return_value=data["catalog"]
        ), mock.patch.object(
            dashboard_services, "read_model_license_acceptances", return_value={}
        ), mock.patch.object(
            dashboard_services.status_monitor, "refresh_now"
        ) as refresh:
            result = dashboard_services.resolve_sweep_request(request, refresh=False)
        cached.assert_called_once_with(refresh=False)
        refresh.assert_not_called()
        self.assertEqual(result.plan.cells[0].model.catalog_id, data["catalog"][0].id)
        self.assertEqual(result.plan.cells[0].workers[0].endpoint_identity, "physical-j1")
        self.assertNotIn(TEXT, result.plan.to_json())

    def test_explicit_refresh_is_the_only_resolver_path_that_refreshes_status(self):
        from cluster.dashboard import services as dashboard_services
        data = fixtures()
        with mock.patch.object(
            dashboard_services, "_cached_sweep_inventories", return_value=data["inventories"]
        ) as cached, mock.patch.object(
            dashboard_services, "_sweep_workers", return_value=data["workers"]
        ), mock.patch.object(
            dashboard_services, "read_model_catalog", return_value=data["catalog"]
        ), mock.patch.object(
            dashboard_services, "read_model_license_acceptances", return_value={}
        ), mock.patch.object(
            dashboard_services.status_monitor, "refresh_now"
        ) as refresh:
            dashboard_services.resolve_sweep_request(request_data(), refresh=True)
        refresh.assert_called_once_with()
        cached.assert_called_once_with(refresh=True)


class RouteFacade:
    def __init__(self, service):
        self.service = service

    def startup(self): pass
    def shutdown(self): pass
    def sweep_capabilities(self): return self.service.capabilities()
    def preview_sweep(self, payload, refresh=False): return self.service.preview(payload, refresh=refresh)
    def save_sweep(self, payload): return self.service.save(payload)
    def sweeps(self, offset=0, limit=100): return self.service.list(offset=offset, limit=limit)
    def sweep(self, sweep_id): return self.service.get(sweep_id)
    def start_sweep(self, sweep_id, payload): return self.service.start(sweep_id, payload)
    def pause_sweep(self, sweep_id, payload): return self.service.pause(sweep_id, payload)
    def resume_sweep(self, sweep_id, payload): return self.service.resume(sweep_id, payload)
    def cancel_sweep(self, sweep_id, payload): return self.service.cancel(sweep_id, payload)
    def retry_sweep_trial(self, sweep_id, trial_id, payload): return self.service.retry(sweep_id, trial_id, payload)
    def sweep_events(self, sweep_id, cursor=0, limit=100): return self.service.events(sweep_id, cursor=cursor, limit=limit)
    def sweep_event_stream(self, sweep_id, cursor=0): return self.service.event_stream(sweep_id, cursor=cursor)
    def sweep_results(self, sweep_id): return self.service.results(sweep_id)
    def export_sweep_plan(self, sweep_id): return self.service.export_plan(sweep_id)
    def export_sweep_results(self, sweep_id, format="json"): return self.service.export_results(sweep_id, format=format)
    def clone_sweep_condition(self, sweep_id, trial_id, new_sweep_id): return self.service.clone_condition(sweep_id, trial_id, new_sweep_id)


class SweepRouteTests(SweepServiceTests):
    @classmethod
    def setUpClass(cls):
        from cluster.dashboard import routes as dashboard_routes

        cls.dashboard_routes = dashboard_routes
        cls.template_root = tempfile.TemporaryDirectory()
        cls.route_counts = []
        seen = set()
        for value in vars(dashboard_routes).values():
            if (
                hasattr(value, "routes")
                and value.__class__.__name__ == "APIRouter"
                and id(value) not in seen
            ):
                seen.add(id(value))
                cls.route_counts.append((value, len(value.routes)))
        app = FastAPI()
        cls.facade = RouteFacade(None)
        app.state.dashboard_services = cls.facade
        dashboard_routes.register_routers(
            app, Jinja2Templates(directory=cls.template_root.name)
        )
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        for router, count in cls.route_counts:
            del router.routes[count:]
        cls.template_root.cleanup()

    def setUp(self):
        super().setUp()
        self.facade.service = self.service

    def test_valid_preview_save_start_list_get_export_and_auth_policy(self):
        body = {"sweep_id": "route_sweep", **request_data()}
        with mock.patch("cluster.dashboard.dependencies.services.read_settings", return_value={"dashboard_token_auth": False}):
            self.assertEqual(self.client.get("/api/sweeps/capabilities").status_code, 200)
            preview = self.client.post("/api/sweeps/preview", json=request_data())
            saved = self.client.post("/api/sweeps/drafts", json=body)
            self.assertEqual(preview.status_code, 200)
            self.assertEqual(saved.status_code, 200)
            start = self.client.post(
                "/api/sweeps/route_sweep/start",
                json={
                    "idempotency_key": "route-start-key",
                    "plan_revision": saved.json()["plan_revision"],
                    "plan_sha256": saved.json()["plan_sha256"],
                },
            )
            self.assertEqual(start.status_code, 200)
            self.assertEqual(self.client.get("/api/sweeps").status_code, 200)
            self.assertEqual(self.client.get("/api/sweeps/route_sweep").status_code, 200)
            exported = self.client.get("/api/sweeps/route_sweep/export-plan")
            self.assertEqual(exported.status_code, 200)
            self.assertEqual(
                exported.headers["content-disposition"],
                'attachment; filename="sweep-plan-route_sweep.json"',
            )
            results_json = self.client.get("/api/sweeps/route_sweep/export-results?format=json")
            self.assertEqual(results_json.status_code, 200)
            self.assertFalse(results_json.json()["formal_approved"])
            results_csv = self.client.get("/api/sweeps/route_sweep/export-results?format=csv")
            self.assertEqual(results_csv.status_code, 200)
            self.assertIn("text/csv", results_csv.headers["content-type"])
            trial_id = start.json()["sweep"]["trials"][0]["trial_id"]
            cloned = self.client.post(
                f"/api/sweeps/route_sweep/trials/{trial_id}/clone-draft",
                json={"new_sweep_id": "route_sweep_clone"},
            )
            self.assertEqual(cloned.status_code, 200)
            self.assertFalse(cloned.json()["started"])
            self.assertEqual(cloned.json()["draft"]["status"], "draft")

        with mock.patch("cluster.dashboard.dependencies.services.read_settings", return_value={"dashboard_token_auth": True}), mock.patch(
            "cluster.dashboard.dependencies.services.dashboard_token_is_valid",
            side_effect=lambda value: value == "valid-token",
        ):
            self.assertEqual(self.client.get("/api/sweeps/capabilities").status_code, 401)
            self.assertEqual(
                self.client.get(
                    "/api/sweeps/capabilities", headers={"X-Cluster-Token": "valid-token"}
                ).status_code,
                200,
            )

    def test_unknown_fields_invalid_axes_huge_grid_and_malicious_ids(self):
        with mock.patch("cluster.dashboard.dependencies.services.read_settings", return_value={"dashboard_token_auth": False}):
            unknown = self.client.post(
                "/api/sweeps/preview", json={**request_data(), "actual_config": {"verified": True}}
            )
            self.assertEqual(unknown.status_code, 422)
            oversized = self.client.post(
                "/api/sweeps/preview",
                content=b"{" + b" " * 1_048_576 + b"}",
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(oversized.status_code, 413)
            invalid = request_data()
            invalid["spec"]["axes"] = [{"name": "n_ctx", "values": [1]}]
            self.assertEqual(self.client.post("/api/sweeps/preview", json=invalid).status_code, 400)
            huge = request_data()
            huge["spec"]["axes"] = [
                {"name": "n_ctx", "values": list(range(128, 192))},
                {"name": "concurrency", "values": list(range(1, 65))},
            ]
            self.assertEqual(self.client.post("/api/sweeps/preview", json=huge).status_code, 400)
            malicious = self.client.get("/api/sweeps/%2E%2E%2Fescape/export-plan")
            self.assertIn(malicious.status_code, {400, 404})
            bad_cursor = self.client.get(
                "/api/sweeps/route_sweep/events/stream", headers={"Last-Event-ID": "not-an-int"}
            )
            self.assertEqual(bad_cursor.status_code, 400)

    def test_http_lifecycle_parallel_overlap_results_and_sse_reconnect(self):
        body = request_data()
        body["spec"]["execution"] = {
            "mode": "disjoint_parallel", "max_parallel_jobs": 2,
            "backfill_policy": "bounded", "backfill_window": 8,
        }
        body = {"sweep_id": "route_lifecycle", **body}
        with mock.patch(
            "cluster.dashboard.dependencies.services.read_settings",
            return_value={"dashboard_token_auth": False},
        ):
            saved_response = self.client.post("/api/sweeps/drafts", json=body)
            self.assertEqual(saved_response.status_code, 200)
            saved = saved_response.json()
            base = {
                "plan_revision": saved["plan_revision"],
                "plan_sha256": saved["plan_sha256"],
            }
            started = self.client.post(
                "/api/sweeps/route_lifecycle/start",
                json={**base, "idempotency_key": "http-start-key"},
            )
            self.assertEqual(started.status_code, 200)
            self.assertEqual(len(self.backend.starts), 1)
            paused = self.client.post(
                "/api/sweeps/route_lifecycle/pause",
                json={**base, "idempotency_key": "http-pause-key", "reason": "http pause"},
            )
            self.assertEqual(paused.status_code, 200)
            job_id = started.json()["sweep"]["trials"][0]["attempts"][0]["backend_job_id"]
            self.backend.finish(job_id)
            self.client.get("/api/sweeps/route_lifecycle")
            resumed = self.client.post(
                "/api/sweeps/route_lifecycle/resume",
                json={**base, "idempotency_key": "http-resume-key"},
            )
            self.assertEqual(resumed.status_code, 200)
            current = self.client.get("/api/sweeps/route_lifecycle").json()["sweep"]
            active = next(item for item in current["trials"] if item["status"] == "running")
            cancelled = self.client.post(
                "/api/sweeps/route_lifecycle/cancel",
                json={**base, "idempotency_key": "http-cancel-key", "reason": "http cancel"},
            )
            self.assertEqual(cancelled.status_code, 200)
            self.assertEqual(self.backend.cancels, [active["attempts"][-1]["backend_job_id"]])
            self.client.get("/api/sweeps/route_lifecycle")
            retried = self.client.post(
                f"/api/sweeps/route_lifecycle/trials/{active['trial_id']}/retry",
                json={**base, "idempotency_key": "http-retry-key", "reason": "evidence cleared"},
            )
            self.assertEqual(retried.status_code, 200)
            self.assertEqual(self.client.get("/api/sweeps/route_lifecycle/events?cursor=1&limit=2").status_code, 200)
            self.assertEqual(self.client.get("/api/sweeps/route_lifecycle/results").status_code, 200)

            observed = {}
            original = self.facade.sweep_event_stream
            def finite_stream(sweep_id, cursor=0):
                observed.update(sweep_id=sweep_id, cursor=cursor)
                return iter(["id: 3\nevent: sweep\ndata: {}\n\n"])
            self.facade.sweep_event_stream = finite_stream
            try:
                streamed = self.client.get(
                    "/api/sweeps/route_lifecycle/events/stream",
                    headers={"Last-Event-ID": "2"},
                )
            finally:
                self.facade.sweep_event_stream = original
            self.assertEqual(streamed.status_code, 200)
            self.assertEqual(observed, {"sweep_id": "route_lifecycle", "cursor": 2})


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cluster.application.model_service import ModelPreflightError, build_direct_install_spec
from cluster.dashboard.schemas import ModelInstallPayload
from cluster.domain.errors import ErrorCode
from cluster.domain.model import DownloadPolicy, ModelCatalogEntry, parse_catalog_entries
from cluster.integrations.legacy_inventory_runtime import Node


ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "cluster" / "config" / "model_catalog.json"


def locked_entry(**overrides: object) -> ModelCatalogEntry:
    values = {
        "id": "locked/model-Q4_K_M.gguf",
        "source_model_repo": "owner/original",
        "gguf_repo": "owner/model-GGUF",
        "gguf_revision": "a" * 40,
        "gguf_filename": "model-Q4_K_M.gguf",
        "size_bytes": 1_000_000,
        "sha256": "b" * 64,
        "quantization": "Q4_K_M",
        "provenance_status": "official",
        "official_gguf": True,
        "license": "Apache-2.0",
        "download_policy": "direct",
    }
    values.update(overrides)
    return ModelCatalogEntry(**values)


class DownloadEligibilityTests(unittest.TestCase):
    def test_direct_download_requires_complete_immutable_identity(self) -> None:
        entry = locked_entry()
        self.assertTrue(entry.download_eligibility["eligible"])
        spec = build_direct_install_spec(entry)
        self.assertEqual(
            spec.source_url,
            "https://huggingface.co/owner/model-GGUF/resolve/" + "a" * 40 + "/model-Q4_K_M.gguf",
        )
        self.assertEqual(spec.expected_sha256, "b" * 64)
        self.assertNotIn("token", spec.source_url)

    def test_policy_and_identity_gaps_are_fail_closed(self) -> None:
        for entry in (
            locked_entry(download_policy=DownloadPolicy.CATALOG_ONLY),
            locked_entry(download_policy=DownloadPolicy.GATED_MANUAL, gated=True),
            locked_entry(download_policy=DownloadPolicy.MULTIPART_UNSUPPORTED, multipart=True),
            locked_entry(gguf_revision="main"),
            locked_entry(sha256=""),
            locked_entry(license_review_required=True),
            locked_entry(gguf_filename="nested/model.gguf"),
        ):
            self.assertFalse(entry.download_eligibility["eligible"])
            with self.assertRaises(ModelPreflightError):
                build_direct_install_spec(entry)

    def test_license_and_gated_access_are_independent_explicit_gates(self) -> None:
        licensed = locked_entry(license="Model Community License", license_review_required=True)
        self.assertFalse(licensed.download_eligibility["eligible"])
        self.assertTrue(build_direct_install_spec(licensed, license_accepted=True).metadata["license_accepted"])

        gated = locked_entry(
            license="Gated Model Terms",
            license_review_required=True,
            gated=True,
        )
        for accepted, access in ((False, False), (True, False), (False, True)):
            with self.assertRaises(ModelPreflightError):
                build_direct_install_spec(gated, license_accepted=accepted, gated_access=access)
        spec = build_direct_install_spec(gated, license_accepted=True, gated_access=True)
        self.assertEqual(spec.repo_id, "owner/model-GGUF")
        self.assertEqual(spec.revision, "a" * 40)
        self.assertEqual(spec.filename, "model-Q4_K_M.gguf")

    def test_legacy_hf_repo_is_a_compatible_download_source(self) -> None:
        entry = locked_entry(gguf_repo="", gguf_revision="", hf_repo="owner/model-GGUF", hf_revision="c" * 40)
        self.assertTrue(entry.download_eligibility["eligible"])
        self.assertIn("/resolve/" + "c" * 40 + "/", build_direct_install_spec(entry).source_url)


class CatalogExpansionTests(unittest.TestCase):
    def test_existing_entries_and_target_families_are_present(self) -> None:
        raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        entries = parse_catalog_entries(raw["models"])
        ids = {entry.id for entry in entries}
        self.assertEqual(len(entries), 34)
        self.assertIn("qwen2.5-1.5b/qwen2.5-1.5b-instruct-q4_k_m.gguf", ids)
        self.assertIn("granite-3.3-8b/granite-3.3-8b-instruct-Q4_K_M.gguf", ids)
        families = {entry.family for entry in entries}
        self.assertTrue({"DeepSeek-R1 Distill", "Gemma 3", "Ministral 3", "Magistral", "Llama 3.3"}.issubset(families))
        self.assertEqual(sum(entry.download_policy is DownloadPolicy.DIRECT for entry in entries), 33)
        direct = [entry for entry in entries if entry.download_eligibility["eligible"]]
        self.assertGreaterEqual(len(direct), 20)
        self.assertTrue(all(entry.identity_locked for entry in entries if entry.download_policy is DownloadPolicy.DIRECT))
        self.assertTrue(all(entry.download_repo and entry.download_revision for entry in entries if entry.download_policy is DownloadPolicy.DIRECT))
        self.assertEqual(sum(entry.gated for entry in entries), 5)
        self.assertGreaterEqual(sum(entry.requires_license_acceptance for entry in entries), 12)
        extreme = next(entry for entry in entries if entry.family == "Llama 3.3")
        self.assertEqual(extreme.download_policy, DownloadPolicy.MULTIPART_UNSUPPORTED)
        self.assertTrue(extreme.multipart)
        self.assertNotEqual(extreme.verification_status.value, "recommended")
        for entry in entries:
            if (entry.parameters_total_b or entry.parameter_count_b or 0) > 8:
                self.assertIn("model_parallel_rpc", entry.benchmark_roles, entry.id)

    def test_new_catalog_records_separate_original_and_gguf_sources(self) -> None:
        entries = parse_catalog_entries(json.loads(CATALOG_PATH.read_text(encoding="utf-8"))["models"])
        ministral = next(entry for entry in entries if entry.family == "Ministral 3" and entry.parameters_total_b == 3)
        self.assertTrue(ministral.source_model_repo)
        self.assertTrue(ministral.gguf_repo)
        self.assertNotEqual(ministral.source_model_repo, ministral.gguf_repo)
        self.assertEqual(ministral.provenance_status.value, "official")
        self.assertTrue(ministral.license)
        self.assertIn("reasoning", {tag.lower() for entry in entries for tag in entry.capability_tags})

    def test_catalog_never_lists_runtime_or_cloud_only_products(self) -> None:
        text = CATALOG_PATH.read_text(encoding="utf-8").lower()
        for forbidden in ('"family":"ollama"', '"family":"gpt"', '"family":"claude"', '"family":"gemini"', '"family":"grok"'):
            self.assertNotIn(forbidden, text)


class CatalogInstallServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.worker = Node("jetson-01", "worker", "192.168.0.26", "jetson", 22, 8000, "/home/jetson/project/llm", True, platform="jetson")

    def test_dashboard_builds_action_from_catalog_not_frontend_url(self) -> None:
        from cluster.dashboard import services

        action = {"id": "action-1", "status": "queued"}
        payload = ModelInstallPayload(nodes=[self.worker.name], source="direct", confirmed=True)
        with mock.patch.object(services, "read_model_catalog", return_value=[locked_entry()]), mock.patch.object(
            services, "read_enabled_nodes", return_value=[self.worker]
        ), mock.patch.object(services, "read_environment_reports", return_value=[]), mock.patch.object(
            services.actions, "start", return_value=action
        ) as start:
            result = services.DashboardFacade().install_catalog_model("locked/model-Q4_K_M.gguf", payload)
        self.assertTrue(result["ok"])
        sent = start.call_args.args[0]
        self.assertEqual(sent.action, "install-model-url")
        self.assertEqual(sent.node_names, ["jetson-01"])
        self.assertEqual(sent.options["source_url"], build_direct_install_spec(locked_entry()).source_url)
        self.assertEqual(sent.options["expected_sha256"], "b" * 64)

    def test_unknown_model_and_insufficient_storage_are_structured(self) -> None:
        from cluster.dashboard import services

        payload = ModelInstallPayload(nodes=[self.worker.name], confirmed=True)
        with mock.patch.object(services, "read_model_catalog", return_value=[]):
            with self.assertRaises(services.DashboardServiceError) as missing:
                services.DashboardFacade().install_catalog_model("missing.gguf", payload)
        self.assertEqual(missing.exception.status_code, 404)
        with mock.patch.object(services, "read_model_catalog", return_value=[locked_entry(size_bytes=10_000_000_000)]), mock.patch.object(
            services, "read_enabled_nodes", return_value=[self.worker]
        ), mock.patch.object(services, "read_environment_reports", return_value=[{"node": self.worker.name, "disk_free_gb": 1.0}]):
            with self.assertRaises(services.DashboardServiceError) as storage:
                services.DashboardFacade().install_catalog_model("locked/model-Q4_K_M.gguf", payload)
        self.assertEqual(storage.exception.detail["code"], ErrorCode.CONFIG_MISMATCH.value)
        self.assertEqual(storage.exception.detail["evidence"]["reason_code"], "MODEL_STORAGE_INSUFFICIENT")

    def test_large_model_requires_one_coordinator_and_no_job_is_created(self) -> None:
        from cluster.dashboard import services

        second = Node("jetson-02", "worker", "192.168.0.27", "jetson", 22, 8000, "/home/jetson/project/llm", True, platform="jetson")
        entry = locked_entry(parameters_total_b=14.0)
        payload = ModelInstallPayload(nodes=[self.worker.name, second.name], confirmed=True)
        with mock.patch.object(services, "read_model_catalog", return_value=[entry]), mock.patch.object(
            services, "read_enabled_nodes", return_value=[self.worker, second]
        ), mock.patch.object(services.experiments, "start") as job_start:
            with self.assertRaises(services.DashboardServiceError):
                services.DashboardFacade().install_catalog_model(entry.id, payload)
        job_start.assert_not_called()

    def test_generic_dashboard_action_cannot_supply_arbitrary_download_url(self) -> None:
        from cluster.dashboard import services

        payload = services.ActionPayload(
            action="install-model-url",
            node_names=[self.worker.name],
            options={
                "confirmed": True,
                "model_id": "evil.gguf",
                "source_url": "https://attacker.invalid/evil.gguf",
                "expected_sha256": "0" * 64,
            },
        )
        with self.assertRaises(services.DashboardServiceError) as blocked:
            services.DashboardFacade().start_action(payload)
        self.assertEqual(blocked.exception.status_code, 400)

    def test_project_local_acceptance_is_revision_bound_and_private(self) -> None:
        from cluster.dashboard import services

        entry = locked_entry(license="Model Community License", license_review_required=True)
        with tempfile.TemporaryDirectory() as temporary:
            acceptance_path = Path(temporary) / "model_license_acceptances.json"
            with mock.patch.object(services, "MODEL_LICENSE_ACCEPTANCE_PATH", acceptance_path):
                initial = services.model_license_status(entry)
                self.assertFalse(initial["accepted"])
                saved = services.write_model_license_acceptance(entry, accepted=True)
                self.assertTrue(saved["accepted"])
                self.assertEqual(stat.S_IMODE(acceptance_path.stat().st_mode), 0o600)
                changed = locked_entry(
                    license="Model Community License",
                    license_review_required=True,
                    gguf_revision="c" * 40,
                )
                self.assertFalse(services.model_license_status(changed)["accepted"])
                document = json.loads(acceptance_path.read_text(encoding="utf-8"))
                self.assertNotIn("token", json.dumps(document).lower())

    def test_experiment_preflight_obeys_current_project_acceptance(self) -> None:
        from cluster.dashboard import services

        entry = locked_entry(license="Model Community License", license_review_required=True)
        with tempfile.TemporaryDirectory() as temporary:
            acceptance_path = Path(temporary) / "model_license_acceptances.json"
            with mock.patch.object(services, "MODEL_LICENSE_ACCEPTANCE_PATH", acceptance_path):
                with self.assertRaises(ModelPreflightError):
                    services.validate_project_model_acceptances([entry.id], {entry.id: entry})
                services.write_model_license_acceptance(entry, accepted=True)
                services.validate_project_model_acceptances([entry.id], {entry.id: entry})
                services.write_model_license_acceptance(entry, accepted=False)
                with self.assertRaises(ModelPreflightError):
                    services.validate_project_model_acceptances([entry.id], {entry.id: entry})

    def test_gated_install_uses_authenticated_controller_cache_without_token_options(self) -> None:
        from cluster.dashboard import services

        entry = locked_entry(
            license="Gated Model Terms",
            license_review_required=True,
            gated=True,
        )
        action = {"id": "action-gated", "status": "queued"}
        payload = ModelInstallPayload(nodes=[self.worker.name], source="direct", confirmed=True)
        accepted = {
            entry.id: {
                "fingerprint": services._model_license_fingerprint(entry),
                "accepted_at": "2026-08-25T00:00:00Z",
            }
        }
        with mock.patch.object(services, "read_model_catalog", return_value=[entry]), mock.patch.object(
            services, "read_model_license_acceptances", return_value=accepted
        ), mock.patch.object(
            services, "huggingface_access_status", return_value={"installed": True, "configured": True, "verified": True, "account": "tester"}
        ), mock.patch.object(services, "read_enabled_nodes", return_value=[self.worker]), mock.patch.object(
            services, "read_environment_reports", return_value=[]
        ), mock.patch.object(services.psutil, "disk_usage", return_value=mock.Mock(free=10_000_000_000)), mock.patch.object(
            services.actions, "start", return_value=action
        ) as start:
            result = services.DashboardFacade().install_catalog_model(entry.id, payload)
        self.assertEqual(result["download_mode"], "controller_authenticated")
        sent = start.call_args.args[0]
        self.assertEqual(sent.action, "install-model-cache")
        self.assertEqual(sent.options["source_repo"], entry.download_repo)
        self.assertEqual(sent.options["source_filename"], entry.gguf_filename)
        self.assertNotIn("token", json.dumps(sent.options).lower())
        self.assertNotIn("source_url", sent.options)


if __name__ == "__main__":
    unittest.main()

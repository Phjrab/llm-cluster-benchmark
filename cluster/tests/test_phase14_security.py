from __future__ import annotations

import contextlib
import io
import json
import os
import secrets
import shlex
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cluster import clusterctl
from cluster.benchmark import runner as benchmark_runner
from cluster.clusterctl import Node
from cluster.dashboard.schemas import ActionPayload, ExperimentPayload
from cluster.domain.errors import DomainValidationError
from cluster.domain.identifiers import validate_model_id
from cluster.domain.worker import validate_worker_project_dir
from cluster.infrastructure.remote import SshRemoteExecutor
from cluster.infrastructure.storage import (
    FilesystemExperimentRepository,
    FilesystemRunRepository,
    FilesystemSuiteRepository,
)


def _worker(name: str, host: str, *, identity_file: str = "") -> Node:
    return Node(
        name=name,
        role="worker",
        host=host,
        user="edge",
        ssh_port=22,
        api_port=8000,
        project_dir=f"/home/edge/{name}/llm-cluster",
        enabled=True,
        identity_file=identity_file,
        platform="jetson",
    )


def _write_inventory(path: Path, *, host: str, identity_file: str = "") -> None:
    path.write_text(
        "name,role,host,user,ssh_port,api_port,project_dir,enabled,identity_file,platform\n"
        f"worker-01,worker,{host},edge,22,8000,/home/edge/worker-01/llm-cluster,true,{identity_file},jetson\n",
        encoding="utf-8",
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class SshBoundarySecurityTests(unittest.TestCase):
    def test_remote_executor_keeps_batch_mode_and_shell_quotes_every_argument(self) -> None:
        node = _worker("worker-01", "192.168.0.26")
        arguments = [
            "printf",
            "%s",
            "value; touch /tmp/should-not-exist",
            "$(id)",
            "quote'\" and space",
            "line\nbreak",
        ]
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with mock.patch(
            "cluster.infrastructure.remote.subprocess.run", return_value=completed
        ) as run:
            result = SshRemoteExecutor().run(node, arguments)

        self.assertTrue(result.ok)
        command = run.call_args.args[0]
        self.assertIn("BatchMode=yes", command)
        self.assertEqual(command[-2], node.ssh_target)
        self.assertEqual(shlex.split(command[-1]), arguments)
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_rsync_transport_keeps_private_identity_as_one_quoted_argument(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            identity = Path(directory) / "cluster identity"
            identity.write_text("private-key-fixture", encoding="utf-8")
            identity.chmod(0o600)
            parts = shlex.split(
                clusterctl._rsync_ssh(
                    _worker("worker-01", "192.168.0.26", identity_file=str(identity))
                )
            )

        self.assertIn("BatchMode=yes", parts)
        self.assertIn("IdentitiesOnly=yes", parts)
        self.assertEqual(parts[parts.index("-i") + 1], str(identity.resolve()))

    def test_public_or_hostname_worker_inventory_is_rejected(self) -> None:
        for host in ("8.8.8.8", "worker.example.com", "169.254.10.20", "100.64.0.10"):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as directory:
                inventory = Path(directory) / "nodes.csv"
                _write_inventory(inventory, host=host)
                with self.assertRaisesRegex(ValueError, "private|LAN|Worker host"):
                    clusterctl.load_nodes(inventory, require_legacy_head=False)

    def test_identity_path_rejects_ambiguous_non_files_and_open_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            directory_identity = root / "identity-dir"
            directory_identity.mkdir()
            missing = root / "missing"
            open_identity = root / "open-key"
            open_identity.write_text("private-key-fixture", encoding="utf-8")
            open_identity.chmod(0o644)
            invalid = (
                "relative/id_ed25519",
                "~/.ssh/phase14-definitely-missing-id_ed25519",
                "$PHASE14_MISSING_IDENTITY/id_ed25519",
                str(missing),
                str(directory_identity),
                str(open_identity),
            )
            for identity in invalid:
                with self.subTest(identity=identity), self.assertRaisesRegex(
                    ValueError, "identity|private|regular|absolute|permission"
                ):
                    with mock.patch.dict(
                        os.environ,
                        {"PHASE14_MISSING_IDENTITY": str(root / "missing-parent")},
                    ):
                        clusterctl.ssh_base(
                            _worker(
                                "worker-01",
                                "192.168.0.26",
                                identity_file=identity,
                            )
                        )

            valid = root / "id_ed25519"
            valid.write_text("private-key-fixture", encoding="utf-8")
            valid.chmod(0o600)
            command = clusterctl.ssh_base(
                _worker("worker-01", "192.168.0.26", identity_file=str(valid))
            )
            self.assertEqual(command[command.index("-i") + 1], str(valid.resolve()))


class PackageAndPathSecurityTests(unittest.TestCase):
    def test_bootstrap_executes_only_fixed_allowlisted_apt_commands(self) -> None:
        node = _worker("worker-01", "192.168.0.26")
        discovery = {
            "ssh": True,
            "project": False,
            "platform_kind": "jetson",
            "board_model": "NVIDIA Jetson Orin Nano",
            "architecture": "aarch64",
            "missing_packages": ["python3-venv", "evil-package;id"],
            "sudo_nopasswd": True,
        }
        commands: list[list[str]] = []

        def run_on_node(_node: Node, arguments: list[str], timeout: int = 120):
            commands.append(list(arguments))
            stdout = "1000\n" if arguments == ["id", "-u"] else ""
            return clusterctl.CommandResult(0, stdout, "")

        with mock.patch.object(clusterctl, "discover_node", return_value=discovery), mock.patch.object(
            clusterctl, "run_on_node", side_effect=run_on_node
        ):
            result = clusterctl.bootstrap_system_one(node)

        self.assertTrue(result["ok"])
        self.assertEqual(commands[0], ["id", "-u"])
        self.assertEqual(commands[1], ["sudo", "-n", "apt-get", "update"])
        self.assertEqual(
            commands[2],
            [
                "sudo",
                "-n",
                "apt-get",
                "install",
                "-y",
                "--no-install-recommends",
                "python3-venv",
            ],
        )
        self.assertFalse(any("evil-package" in part for command in commands for part in command))
        self.assertTrue(
            all(
                command == ["id", "-u"]
                or command[:3] == ["sudo", "-n", "apt-get"]
                for command in commands
            )
        )

    def test_project_and_model_paths_reject_escape_or_ambiguous_values(self) -> None:
        for project_dir in (
            "/",
            "/home",
            "/home/edge",
            "/home/edge/../root/project",
            "/tmp/llm-cluster",
            "relative/project",
        ):
            with self.subTest(project_dir=project_dir), self.assertRaises(
                DomainValidationError
            ):
                validate_worker_project_dir(project_dir, "edge")

        for model_id in (
            "../escape.gguf",
            "/absolute/model.gguf",
            "models/../../escape.gguf",
            "models\\escape.gguf",
            "model.bin",
            "model.gguf\nsecond",
        ):
            with self.subTest(model_id=model_id), self.assertRaises(
                DomainValidationError
            ):
                validate_model_id(model_id)

    def test_worker_setup_rejects_broad_project_path_before_plan(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        completed = subprocess.run(
            [
                "bash",
                str(project_root / "cluster" / "worker_setup.sh"),
                "--plan-only",
                "--platform",
                "jetson",
                "--project-dir",
                "/",
            ],
            cwd=project_root,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertRegex(
            completed.stderr,
            r"(?i)project.*(unsafe|unambiguous|dedicated|broad)",
        )


class TokenAndArtifactSecurityTests(unittest.TestCase):
    def test_dashboard_runtime_tightens_existing_sensitive_artifacts(self) -> None:
        from cluster.dashboard import services

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            results = root / "results-override"
            experiments = runtime / "experiments"
            environment = runtime / "environment"
            jobs = root / "jobs-override"
            suites = results / "_suites"
            run_dir = results / "20260820_120000_ab12"
            private_directories = (
                runtime,
                results,
                suites,
                experiments,
                environment,
                jobs,
                run_dir,
            )
            for path in private_directories:
                path.mkdir(parents=True, exist_ok=True)
                path.chmod(0o755)

            inventory = runtime / "nodes.csv"
            token = runtime / "dashboard.token"
            settings = runtime / "settings.json"
            cache = runtime / "model_catalog.cache.json"
            private_files = (
                inventory,
                token,
                settings,
                cache,
                experiments / "private_experiment.json",
                environment / "worker-01.json",
                jobs / "job_01.json",
                jobs / "job_01.events.jsonl",
                suites / "private_suite.json",
                *(run_dir / name for name in services.PRIVATE_RUN_ARTIFACTS),
            )
            inventory.write_text(
                "name,role,host,user,ssh_port,api_port,project_dir,enabled\n",
                encoding="utf-8",
            )
            token.write_text("dashboard-secret\n", encoding="utf-8")
            settings.write_text(
                '{"worker_api_auth": false, "dashboard_token_auth": false}\n',
                encoding="utf-8",
            )
            for path in private_files:
                if not path.exists():
                    path.write_text('{"private": true}\n', encoding="utf-8")
                path.chmod(0o644)

            with mock.patch.multiple(
                services,
                RUNTIME_DIR=runtime,
                RESULTS_DIR=results,
                EXPERIMENTS_DIR=experiments,
                ENVIRONMENT_DIR=environment,
                JOBS_DIR=jobs,
                INVENTORY_PATH=inventory,
                TOKEN_PATH=token,
                SETTINGS_PATH=settings,
                MODEL_CATALOG_CACHE_PATH=cache,
            ):
                services.ensure_runtime()

            self.assertEqual(
                {str(path.relative_to(root)): oct(_mode(path)) for path in private_directories},
                {str(path.relative_to(root)): "0o700" for path in private_directories},
            )
            self.assertEqual(
                {str(path.relative_to(root)): oct(_mode(path)) for path in private_files},
                {str(path.relative_to(root)): "0o600" for path in private_files},
            )

    def test_corrupt_settings_cannot_silently_disable_worker_auth(self) -> None:
        from cluster.dashboard import services as dashboard_services

        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "settings.json"
            settings.write_text("{broken", encoding="utf-8")
            environment = dict(os.environ)
            environment.pop("CLUSTER_WORKER_AUTH", None)
            with mock.patch.object(
                clusterctl, "DEFAULT_SETTINGS", settings
            ), mock.patch.dict(os.environ, environment, clear=True):
                self.assertTrue(clusterctl.worker_auth_enabled())
            with mock.patch.object(dashboard_services, "SETTINGS_PATH", settings):
                self.assertTrue(dashboard_services.read_settings()["worker_api_auth"])

    def test_worker_token_is_repaired_to_private_mode_without_logging_secret(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "worker.token"
            token_path.write_text("top-secret-token\n", encoding="utf-8")
            token_path.chmod(0o644)
            output = io.StringIO()
            errors = io.StringIO()
            with mock.patch.object(
                clusterctl, "DEFAULT_WORKER_TOKEN", token_path
            ), mock.patch.dict(
                os.environ, {"CLUSTER_API_TOKEN": "top-secret-token"}
            ), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertEqual(clusterctl.ensure_worker_token(), "top-secret-token")

            self.assertEqual(_mode(token_path), 0o600)
            self.assertNotIn("top-secret-token", output.getvalue())
            self.assertNotIn("top-secret-token", errors.getvalue())

    def test_dashboard_and_worker_auth_use_constant_time_comparison(self) -> None:
        from fastapi.testclient import TestClient

        from cluster.dashboard import services as dashboard_services
        from cluster.worker import app as worker_app

        with mock.patch.object(
            dashboard_services.secrets,
            "compare_digest",
            wraps=secrets.compare_digest,
        ) as compare:
            with mock.patch.object(
                dashboard_services, "DASHBOARD_TOKEN", "dashboard-secret"
            ):
                self.assertTrue(
                    dashboard_services.dashboard_token_is_valid("dashboard-secret")
                )
            compare.assert_called_once_with("dashboard-secret", "dashboard-secret")

        class Backend:
            def list_models(self):
                return []

            def current_model_info(self):
                return {"loaded": False, "model_id": None}

            def readiness(self):
                return {"ready": True, "error": None}

        class Telemetry:
            def start(self):
                return None

            def stop(self):
                return None

            def status(self):
                return {
                    "provider": "test",
                    "ready": True,
                    "degraded": False,
                    "error": None,
                }

            def snapshot(self):
                return {}

        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            worker_app.secrets,
            "compare_digest",
            wraps=secrets.compare_digest,
        ) as compare:
            app = worker_app.create_app(
                backend=Backend(),
                telemetry=Telemetry(),
                project_root=Path(directory),
                environment={
                    "CLUSTER_PLATFORM": "generic-linux",
                    "CLUSTER_NODE_NAME": "worker-01",
                    "CLUSTER_NODE_ROLE": "worker",
                    "CLUSTER_WORKER_AUTH": "true",
                    "CLUSTER_API_TOKEN": "worker-secret",
                },
            )
            client = TestClient(app)
            denied = client.get(
                "/cluster/health",
                headers={"X-Cluster-Worker-Token": "supplied-secret"},
            )
            self.assertEqual(denied.status_code, 401)
            self.assertNotIn("supplied-secret", denied.text)
            compare.assert_called_once_with("supplied-secret", "worker-secret")

    def test_worker_platform_telemetry_starts_and_stops_with_asgi_lifespan(self) -> None:
        from fastapi.testclient import TestClient

        from cluster.worker import app as worker_app

        class Backend:
            def list_models(self):
                return []

            def current_model_info(self):
                return {"loaded": False, "model_id": None}

            def readiness(self):
                return {"ready": True, "error": None}

        class CountingTelemetry:
            def __init__(self) -> None:
                self.start_calls = 0
                self.stop_calls = 0

            def start(self) -> None:
                self.start_calls += 1

            def stop(self) -> None:
                self.stop_calls += 1

            def status(self):
                return {
                    "provider": "test",
                    "ready": True,
                    "degraded": False,
                    "error": None,
                }

            def snapshot(self):
                return {}

        telemetry = CountingTelemetry()
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            worker_app.TelemetryService,
            "for_platform",
            return_value=telemetry,
        ), mock.patch.object(
            worker_app,
            "runtime_backend",
            return_value={"verified": True, "kind": "cpu", "gpu_offload": False},
        ), mock.patch.object(worker_app, "system_profile", return_value={}):
            app = worker_app.create_app(
                backend=Backend(),
                project_root=Path(directory),
                environment={
                    "CLUSTER_PLATFORM": "generic-linux",
                    "CLUSTER_NODE_NAME": "worker-01",
                    "CLUSTER_NODE_ROLE": "worker",
                    "CLUSTER_WORKER_AUTH": "false",
                },
            )
            self.assertEqual(telemetry.start_calls, 0)
            with TestClient(app) as client:
                self.assertEqual(client.get("/cluster/health").status_code, 200)
                self.assertEqual(telemetry.start_calls, 1)
                self.assertEqual(telemetry.stop_calls, 0)
            self.assertEqual(telemetry.stop_calls, 1)

    def test_new_run_and_catalog_artifacts_are_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = FilesystemRunRepository(root / "results")
            run_id = "20260820_120000_ab12"
            run_dir = runs.create(run_id, {"prompt": "private patient prompt"})
            runs.append_event(
                run_id,
                {"type": "run_started", "prompt": "private patient prompt"},
            )
            runs.write_requests(
                run_id,
                [
                    {
                        "request_id": 1,
                        "node": "worker-01",
                        "error": "private failure detail",
                    }
                ],
            )
            runs.append_response(
                run_id,
                {"request_id": 1, "response": "private model response"},
            )
            runs.write_summary(
                run_id,
                {"run_id": run_id, "error": "private failure detail"},
            )

            experiments = FilesystemExperimentRepository(root / "experiments")
            experiments.write(
                "private_experiment", {"prompt": "private patient prompt"}
            )
            suites = FilesystemSuiteRepository(root / "results" / "_suites")
            suites.write(
                "private_suite",
                {"errors": [{"error": "private failure detail"}]},
            )

            private_files = (
                run_dir / "config.json",
                run_dir / "events.jsonl",
                run_dir / "requests.csv",
                run_dir / "responses.jsonl",
                run_dir / "summary.json",
                root / "experiments" / "private_experiment.json",
                root / "results" / "_suites" / "private_suite.json",
            )
            self.assertEqual(
                {path.name: oct(_mode(path)) for path in private_files},
                {path.name: "0o600" for path in private_files},
            )


class RpcAndLoggingSecurityTests(unittest.TestCase):
    def test_unexpected_dashboard_500_does_not_expose_exception_text(self) -> None:
        from fastapi.testclient import TestClient

        from cluster.dashboard import app as dashboard_app

        application = dashboard_app.create_app()
        private_detail = "phase14-private-path-and-credential-fixture"

        @application.get("/phase14/unexpected-error")
        async def unexpected_error_fixture():
            raise RuntimeError(private_detail)

        response = TestClient(
            application, raise_server_exceptions=False
        ).get("/phase14/unexpected-error")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn(private_detail, response.text)
        payload = response.json()
        self.assertEqual(payload["detail"], "Internal server error")
        self.assertRegex(payload["request_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(response.headers["X-Request-ID"], payload["request_id"])

    def test_dashboard_blocks_rpc_while_worker_auth_is_enabled(self) -> None:
        from cluster.dashboard import services

        workers = [
            _worker("worker-01", "192.168.0.26"),
            _worker("worker-02", "192.168.0.27"),
        ]
        payload = ExperimentPayload(
            name="rpc-security",
            node_names=[worker.name for worker in workers],
            model_ids=["models/example.gguf"],
            prompt="test",
            n_gpu_layers=0,
            execution_strategy="model_parallel_rpc",
            acknowledge_experimental_rpc=True,
        )
        status = [
            {
                "name": worker.name,
                "api": True,
                "profile": {"platform_kind": "jetson"},
            }
            for worker in workers
        ]
        facade = services.DashboardFacade()
        with mock.patch.object(
            services.experiments, "active", return_value=None
        ), mock.patch.object(
            services.actions, "busy_nodes", return_value=[]
        ), mock.patch.object(
            services.status_monitor, "snapshot", return_value=status
        ), mock.patch.object(
            services, "read_all_nodes", return_value=workers
        ), mock.patch.object(
            services, "read_environment_reports", return_value=[]
        ), mock.patch.object(
            services, "validate_experiment_environment"
        ), mock.patch.object(
            services,
            "read_settings",
            return_value={"worker_api_auth": True, "dashboard_token_auth": False},
        ), mock.patch.object(
            services, "collect_worker_model_inventories"
        ) as collect:
            with self.assertRaises(services.DashboardServiceError) as raised:
                facade.start_experiment(payload)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("인증 없는 llama.cpp RPC", str(raised.exception.detail))
        collect.assert_not_called()

    def test_direct_benchmark_runner_also_blocks_rpc_with_worker_auth(self) -> None:
        workers = [
            _worker("worker-01", "192.168.0.26"),
            _worker("worker-02", "192.168.0.27"),
        ]
        config = benchmark_runner.ExperimentConfig(
            name="rpc-security",
            node_names=[worker.name for worker in workers],
            model_id="models/example.gguf",
            prompt="test",
            n_gpu_layers=0,
            requests=1,
            concurrency=1,
            warmup_requests=0,
            execution_strategy="model_parallel_rpc",
            acknowledge_experimental_rpc=True,
        )
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ, {"CLUSTER_WORKER_AUTH": "true"}
        ), mock.patch.object(
            benchmark_runner, "load_nodes", return_value=workers
        ), mock.patch.object(
            benchmark_runner,
            "_rpc_runtime_command",
            side_effect=AssertionError("RPC runtime was reached before auth guard"),
        ):
            with self.assertRaisesRegex(ValueError, "auth|보안|인증"):
                benchmark_runner.run_experiment(
                    config,
                    inventory_path=Path(directory) / "unused.csv",
                    results_root=Path(directory) / "results",
                )

    def test_action_records_do_not_retain_credentials_or_tokens(self) -> None:
        from cluster.dashboard import services

        manager = services.ActionManager()
        node = _worker("worker-01", "192.168.0.26")
        payload = ActionPayload(
            action="install-model-url",
            node_names=[node.name],
            options={
                "confirmed": True,
                "model_id": "models/example.gguf",
                "source_url": "https://user:password@example.invalid/model.gguf",
                "expected_sha256": "a" * 64,
                "dashboard_token": "dashboard-secret",
            },
        )
        with mock.patch.object(
            services, "read_enabled_nodes", return_value=[node]
        ), mock.patch.object(
            services.experiments, "active", return_value=None
        ), mock.patch.object(services.threading, "Thread"):
            record = manager.start(payload)

        serialized = json.dumps(record, sort_keys=True)
        self.assertNotIn("password", serialized)
        self.assertNotIn("dashboard-secret", serialized)
        self.assertNotIn("source_url", serialized)
        self.assertNotIn("expected_sha256", serialized)


if __name__ == "__main__":
    unittest.main()

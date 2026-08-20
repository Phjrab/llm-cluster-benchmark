"""Regression tests for identity-checked Worker and legacy server lifecycle."""

from __future__ import annotations

import json
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cluster.infrastructure.process import ProcessIdentity, can_signal
from cluster.infrastructure.process_guard import (
    EXIT_STOPPED,
    ProcessGuard,
    ProcessGuardError,
    ServiceSpec,
    main,
)


ROOT = Path(__file__).resolve().parents[2]
_KEEP_PROCESS = object()


class FakeInspector:
    def __init__(self, identity: ProcessIdentity | None) -> None:
        self.identity = identity
        self.signals: list[int] = []
        self.after_term: ProcessIdentity | None | object = _KEEP_PROCESS
        self.refuse_signal = False

    def inspect(self, pid: int) -> ProcessIdentity | None:
        if self.identity is None or self.identity.pid != pid:
            return None
        return self.identity

    def signal(self, expected: ProcessIdentity, signum: int) -> bool:
        if self.refuse_signal or self.identity is None or not can_signal(expected, self.identity):
            return False
        self.signals.append(signum)
        if signum == signal.SIGTERM:
            if self.after_term is _KEEP_PROCESS:
                return True
            self.identity = self.after_term  # type: ignore[assignment]
        elif signum == signal.SIGKILL:
            self.identity = None
        return True

def make_spec(root: Path, *, module: str = "cluster.worker.app", port: int = 8123) -> ServiceSpec:
    return ServiceSpec.create(
        pid_file=root / ".run" / "service.pid",
        identity_file=root / ".run" / "service.identity.json",
        cwd=root,
        python_bin=Path(sys.executable),
        module=module,
        host="127.0.0.1",
        port=port,
    )


def service_identity(
    spec: ServiceSpec,
    *,
    pid: int = 43210,
    started_at: float = 1234.5,
    argv_tail: tuple[str, ...] | None = None,
) -> ProcessIdentity:
    return ProcessIdentity(
        pid=pid,
        executable=str(spec.python_bin),
        cwd=str(spec.cwd),
        argv=(str(spec.python_bin), *(argv_tail or spec.module_argv_tail)),
        started_at=started_at,
        user=spec.user,
    )


def foreign_identity(expected: ProcessIdentity) -> ProcessIdentity:
    return ProcessIdentity(
        pid=expected.pid,
        executable=expected.executable,
        cwd=expected.cwd,
        argv=(expected.executable, "-c", "import time; time.sleep(60)"),
        started_at=expected.started_at + 1.0,
        user=expected.user,
    )


class MetadataAndAdoptionTests(unittest.TestCase):
    def test_listener_ownership_is_bound_to_the_recorded_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = make_spec(root)
            identity = service_identity(spec)
            inspector = FakeInspector(identity)
            guard = ProcessGuard(spec, inspector=inspector)
            guard.record(identity.pid, timeout=0)
            listener = SimpleNamespace(
                status="LISTEN", laddr=SimpleNamespace(port=spec.port)
            )
            foreign_port = SimpleNamespace(
                status="LISTEN", laddr=SimpleNamespace(port=spec.port + 1)
            )

            with mock.patch(
                "cluster.infrastructure.process_guard.psutil.Process"
            ) as process:
                process.return_value.net_connections.return_value = [listener]
                self.assertTrue(guard.owns_listening_tcp_port(identity, spec.port))
                process.return_value.net_connections.return_value = [foreign_port]
                self.assertFalse(guard.owns_listening_tcp_port(identity, spec.port))

    def test_cli_accepts_launcher_argument_order_and_reports_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = main(
                [
                    "--pid-file",
                    str(root / ".run" / "service.pid"),
                    "--identity-file",
                    str(root / ".run" / "service.identity.json"),
                    "--cwd",
                    str(root),
                    "--python",
                    sys.executable,
                    "--module",
                    "cluster.worker.app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8123",
                    "status",
                    "--adopt",
                ]
            )
        self.assertEqual(result, EXIT_STOPPED)

    def test_record_is_atomic_private_and_round_trips_full_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = make_spec(root)
            identity = service_identity(spec)
            guard = ProcessGuard(spec, inspector=FakeInspector(identity))

            saved = guard.record(identity.pid, timeout=0)
            located = guard.locate()
            document = json.loads(spec.identity_file.read_text(encoding="utf-8"))

            self.assertEqual(saved, identity)
            self.assertEqual(located, identity)
            self.assertEqual(document["identity"], identity.to_dict())
            self.assertEqual(document["service"], spec.service_document())
            self.assertEqual(stat.S_IMODE(spec.pid_file.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(spec.identity_file.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(spec.pid_file.parent.stat().st_mode), 0o700)
            self.assertFalse(list(spec.pid_file.parent.glob(".*.tmp-*")))

    def test_record_waits_for_nohup_exec_transition_before_persisting(self) -> None:
        class TransitionInspector(FakeInspector):
            def __init__(self, before: ProcessIdentity, after: ProcessIdentity) -> None:
                super().__init__(before)
                self.after = after
                self.inspections = 0

            def inspect(self, pid: int) -> ProcessIdentity | None:
                self.inspections += 1
                if self.inspections > 1:
                    self.identity = self.after
                return super().inspect(pid)

        with tempfile.TemporaryDirectory() as directory:
            spec = make_spec(Path(directory))
            expected = service_identity(spec)
            inspector = TransitionInspector(foreign_identity(expected), expected)
            guard = ProcessGuard(spec, inspector=inspector, sleep=lambda _seconds: None)

            self.assertEqual(guard.record(expected.pid, timeout=1), expected)
            self.assertGreaterEqual(inspector.inspections, 2)
            self.assertEqual(guard.locate(), expected)

    def test_pid_only_legacy_console_process_is_adopted_only_after_full_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = make_spec(root, module="web.app")
            identity = service_identity(spec, argv_tail=spec.console_argv_tail)
            spec.pid_file.parent.mkdir(parents=True)
            spec.pid_file.write_text(f"{identity.pid}\n", encoding="utf-8")
            spec.pid_file.chmod(0o644)
            guard = ProcessGuard(spec, inspector=FakeInspector(identity))

            self.assertEqual(guard.locate(adopt=True), identity)
            self.assertTrue(spec.identity_file.is_file())
            self.assertEqual(stat.S_IMODE(spec.pid_file.stat().st_mode), 0o600)

    def test_stale_process_metadata_is_removed_without_any_signal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = make_spec(root)
            identity = service_identity(spec)
            inspector = FakeInspector(identity)
            guard = ProcessGuard(spec, inspector=inspector)
            guard.record(identity.pid, timeout=0)
            inspector.identity = None

            self.assertIsNone(guard.locate())
            self.assertEqual(inspector.signals, [])
            self.assertFalse(spec.pid_file.exists())
            self.assertFalse(spec.identity_file.exists())


class RefusalAndSignalTests(unittest.TestCase):
    def test_malformed_pid_and_identity_mismatch_never_signal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = make_spec(root)
            identity = service_identity(spec)
            inspector = FakeInspector(identity)
            guard = ProcessGuard(spec, inspector=inspector)
            spec.pid_file.parent.mkdir(parents=True)
            spec.pid_file.write_text("12; kill 1\n", encoding="utf-8")
            with self.assertRaisesRegex(ProcessGuardError, "malformed PID"):
                guard.stop()
            self.assertEqual(inspector.signals, [])

            spec.pid_file.unlink()
            guard.record(identity.pid, timeout=0)
            inspector.identity = foreign_identity(identity)
            with self.assertRaisesRegex(ProcessGuardError, "identity changed|not this service"):
                guard.stop()
            self.assertEqual(inspector.signals, [])
            self.assertTrue(spec.pid_file.exists())
            self.assertTrue(spec.identity_file.exists())

    def test_term_then_kill_is_bounded_and_rechecks_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            spec = make_spec(Path(directory))
            identity = service_identity(spec)
            inspector = FakeInspector(identity)
            inspector.after_term = _KEEP_PROCESS
            guard = ProcessGuard(spec, inspector=inspector, sleep=lambda _seconds: None)

            guard.terminate_identity(identity, term_timeout=0, kill_timeout=0)
            self.assertEqual(inspector.signals, [signal.SIGTERM, signal.SIGKILL])
            self.assertIsNone(inspector.identity)

    def test_pid_reuse_after_term_never_receives_kill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            spec = make_spec(Path(directory))
            identity = service_identity(spec)
            replacement = foreign_identity(identity)
            inspector = FakeInspector(identity)
            inspector.after_term = replacement
            guard = ProcessGuard(spec, inspector=inspector, sleep=lambda _seconds: None)

            guard.terminate_identity(identity, term_timeout=1, kill_timeout=0)
            self.assertEqual(inspector.signals, [signal.SIGTERM])
            self.assertEqual(inspector.identity, replacement)

    def test_real_foreign_python_process_is_not_signalled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = make_spec(root)
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                with self.assertRaisesRegex(ProcessGuardError, "not this service"):
                    ProcessGuard(spec).terminate_candidate(child.pid)
                self.assertIsNone(child.poll())
            finally:
                child.terminate()
                child.wait(timeout=5)


class LauncherSourceSafetyTests(unittest.TestCase):
    def test_all_launchers_delegate_signals_and_have_no_wildcard_process_kill(self) -> None:
        scripts = (
            ROOT / "cluster" / "worker" / "start.sh",
            ROOT / "cluster" / "worker" / "stop.sh",
            ROOT / "start_server.sh",
            ROOT / "stop_server.sh",
        )
        forbidden = ("pgrep", "pkill", "killall")
        for script in scripts:
            with self.subTest(script=script.name):
                source = script.read_text(encoding="utf-8")
                self.assertIn("cluster.infrastructure.process_guard", source)
                self.assertIn("identity", source.lower())
                self.assertNotIn("kill -0", source)
                self.assertFalse(any(command in source for command in forbidden))
                if script.name.endswith("start.sh") or script.name == "start_server.sh":
                    self.assertIn('chmod 600 "$LOG_FILE"', source)
                    self.assertIn("owns-port", source)
        self.assertNotIn("kill ", (ROOT / "stop_server.sh").read_text(encoding="utf-8"))

    def test_worker_health_hides_token_from_curl_argv_and_repairs_permissions(self) -> None:
        source = (ROOT / "cluster" / "worker" / "start.sh").read_text(encoding="utf-8")
        self.assertIn('chmod 600 "$TOKEN_FILE"', source)
        self.assertIn("curl --config -", source)
        self.assertIn("X-Cluster-Worker-Token: %s", source)
        self.assertNotIn('curl -fsS -H "X-Cluster-Worker-Token:', source)
        self.assertNotIn('-H "X-Cluster-Worker-Token: $CLUSTER_API_TOKEN"', source)


if __name__ == "__main__":
    unittest.main()

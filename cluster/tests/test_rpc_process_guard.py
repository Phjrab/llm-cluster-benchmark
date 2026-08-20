"""Native RPC process identity and shell lifecycle security contracts."""

from __future__ import annotations

import contextlib
import io
import json
import os
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from cluster.infrastructure.process import ProcessIdentity, can_signal
from cluster.infrastructure.process_guard import (
    EXIT_UNSAFE,
    NativeServiceSpec,
    ProcessGuard,
    ProcessGuardError,
    main,
)


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_SCRIPT = ROOT / "cluster" / "rpc" / "runtime.sh"


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def native_cli(
    root: Path,
    argv: list[str],
    *,
    from_record: bool = False,
) -> list[str]:
    arguments = [
        "--pid-file",
        str(root / ".run" / "cluster" / "rpc.pid"),
        "--identity-file",
        str(root / ".run" / "cluster" / "rpc.identity.json"),
        "--cwd",
        str(root),
        "--executable",
        argv[0],
    ]
    if from_record:
        arguments.append("--from-record")
    else:
        arguments.extend(("--argv-json", json.dumps(argv)))
    return arguments


class NativeProcessGuardIntegrationTests(unittest.TestCase):
    def spawn_sleep(self, root: Path) -> tuple[subprocess.Popen[bytes], list[str]]:
        sleep_bin = shutil.which("sleep")
        if sleep_bin is None:
            self.skipTest("sleep executable is unavailable")
        argv = [sleep_bin, "30"]
        child = subprocess.Popen(
            argv,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        self.addCleanup(self._cleanup_child, child)
        return child, argv

    @staticmethod
    def _cleanup_child(child: subprocess.Popen[bytes]) -> None:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)

    def test_native_record_round_trips_exact_argv_and_private_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child, argv = self.spawn_sleep(root)
            explicit = native_cli(root, argv)
            recorded = native_cli(root, argv, from_record=True)
            output = io.StringIO()
            errors = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertEqual(main([*explicit, "record", "--pid", str(child.pid)]), 0)
                self.assertEqual(main([*recorded, "status"]), 0)

            pid_path = root / ".run" / "cluster" / "rpc.pid"
            identity_path = root / ".run" / "cluster" / "rpc.identity.json"
            document = json.loads(identity_path.read_text(encoding="utf-8"))
            restored = NativeServiceSpec.from_record(
                pid_file=pid_path,
                identity_file=identity_path,
                cwd=root,
                executable=Path(argv[0]),
            )

            self.assertEqual(restored.argv, tuple(argv))
            self.assertEqual(document["service"], restored.service_document())
            self.assertEqual(document["identity"]["pid"], child.pid)
            self.assertEqual(document["identity"]["argv"], argv)
            self.assertEqual(mode(root / ".run" / "cluster"), 0o700)
            self.assertEqual(mode(pid_path), 0o600)
            self.assertEqual(mode(identity_path), 0o600)

            reaper = threading.Thread(target=child.wait)
            reaper.start()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertEqual(main([*recorded, "stop"]), 0)
            reaper.join(timeout=5)
            self.assertFalse(reaper.is_alive())
            self.assertFalse(pid_path.exists())
            self.assertFalse(identity_path.exists())

    def test_pid_only_requires_known_exact_argv_before_adoption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child, argv = self.spawn_sleep(root)
            pid_path = root / ".run" / "cluster" / "rpc.pid"
            pid_path.parent.mkdir(parents=True)
            pid_path.write_text(f"{child.pid}\n", encoding="utf-8")
            pid_path.chmod(0o644)
            output = io.StringIO()
            errors = io.StringIO()

            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                result = main([*native_cli(root, argv, from_record=True), "stop"])
            self.assertEqual(result, EXIT_UNSAFE)
            self.assertIsNone(child.poll())
            self.assertFalse((pid_path.parent / "rpc.identity.json").exists())
            errors.seek(0)
            errors.truncate(0)

            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertEqual(
                    main([*native_cli(root, argv), "status", "--adopt"]),
                    0,
                )
                reaper = threading.Thread(target=child.wait)
                reaper.start()
                self.assertEqual(
                    main([*native_cli(root, argv, from_record=True), "stop"]),
                    0,
                    errors.getvalue(),
                )
            reaper.join(timeout=5)
            self.assertFalse(reaper.is_alive())
            self.assertEqual(mode(pid_path.parent), 0o700)

    def test_wrong_native_argv_never_adopts_or_signals_pid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child, argv = self.spawn_sleep(root)
            pid_path = root / ".run" / "cluster" / "rpc.pid"
            pid_path.parent.mkdir(parents=True)
            pid_path.write_text(f"{child.pid}\n", encoding="utf-8")
            wrong = [*argv[:-1], "import time; time.sleep(31)"]
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                result = main([*native_cli(root, wrong), "status", "--adopt"])

            self.assertEqual(result, EXIT_UNSAFE)
            self.assertIsNone(child.poll())
            self.assertFalse((pid_path.parent / "rpc.identity.json").exists())

    def test_from_record_rejects_changed_fixed_executable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child, argv = self.spawn_sleep(root)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(
                    main([*native_cli(root, argv), "record", "--pid", str(child.pid)]),
                    0,
                )
            with self.assertRaisesRegex(ProcessGuardError, r"argv\[0\]|metadata"):
                NativeServiceSpec.from_record(
                    pid_file=root / ".run" / "cluster" / "rpc.pid",
                    identity_file=root / ".run" / "cluster" / "rpc.identity.json",
                    cwd=root,
                    executable=Path("/bin/sh"),
                )
            self.assertIsNone(child.poll())

    def test_from_record_is_limited_to_status_and_stop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child, argv = self.spawn_sleep(root)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(
                    main([*native_cli(root, argv), "record", "--pid", str(child.pid)]),
                    0,
                )
                self.assertEqual(
                    main(
                        [
                            *native_cli(root, argv, from_record=True),
                            "terminate-candidate",
                            "--pid",
                            str(child.pid),
                        ]
                    ),
                    EXIT_UNSAFE,
                )
            self.assertIsNone(child.poll())

    def test_native_configuration_requires_absolute_non_string_argv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sleep_bin = shutil.which("sleep")
            if sleep_bin is None:
                self.skipTest("sleep executable is unavailable")
            common = {
                "pid_file": root / ".run" / "cluster" / "rpc.pid",
                "identity_file": root / ".run" / "cluster" / "rpc.identity.json",
                "cwd": root,
                "executable": Path(sleep_bin),
            }
            with self.assertRaisesRegex(ProcessGuardError, "string list"):
                NativeServiceSpec.create(**common, argv=sleep_bin)  # type: ignore[arg-type]
            with self.assertRaisesRegex(ProcessGuardError, "absolute"):
                NativeServiceSpec.create(**common, argv=["sleep", "30"])

    def test_repeated_native_stop_tolerates_post_term_inspection_transition(self) -> None:
        for iteration in range(5):
            with self.subTest(iteration=iteration), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                child, argv = self.spawn_sleep(root)
                explicit = native_cli(root, argv)
                recorded = native_cli(root, argv, from_record=True)
                errors = io.StringIO()
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                    errors
                ):
                    self.assertEqual(
                        main([*explicit, "record", "--pid", str(child.pid)]),
                        0,
                    )
                    reaper = threading.Thread(target=child.wait)
                    reaper.start()
                    self.assertEqual(main([*recorded, "stop"]), 0, errors.getvalue())
                reaper.join(timeout=5)
                self.assertFalse(reaper.is_alive())


class SequencedInspector:
    def __init__(
        self, identity: ProcessIdentity, post_term: list[ProcessIdentity | None | Exception]
    ) -> None:
        self.identity = identity
        self.post_term = list(post_term)
        self.term_sent = False
        self.signals: list[int] = []

    def inspect(self, pid: int) -> ProcessIdentity | None:
        if pid != self.identity.pid:
            return None
        if self.term_sent and self.post_term:
            value = self.post_term.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        return self.identity

    def signal(self, expected: ProcessIdentity, signum: int) -> bool:
        if not can_signal(expected, self.identity):
            return False
        self.signals.append(signum)
        if signum == signal.SIGTERM:
            self.term_sent = True
        return True


class StepClock:
    def __init__(self, step: float = 5.0) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        self.value += self.step
        return self.value


class PostSignalInspectionTests(unittest.TestCase):
    def make_guard(
        self,
        root: Path,
        post_term: list[ProcessIdentity | None | Exception],
    ) -> tuple[ProcessGuard, NativeServiceSpec, SequencedInspector]:
        sleep_bin = shutil.which("sleep")
        if sleep_bin is None:
            self.skipTest("sleep executable is unavailable")
        spec = NativeServiceSpec.create(
            pid_file=root / ".run" / "cluster" / "rpc.pid",
            identity_file=root / ".run" / "cluster" / "rpc.identity.json",
            cwd=root,
            executable=Path(sleep_bin),
            argv=[sleep_bin, "30"],
        )
        identity = ProcessIdentity(
            pid=43210,
            executable=str(spec.executable),
            cwd=str(spec.cwd),
            argv=spec.argv,
            started_at=1234.5,
            user=spec.user,
        )
        inspector = SequencedInspector(identity, post_term)
        guard = ProcessGuard(
            spec,
            inspector=inspector,
            clock=StepClock(),
            sleep=lambda _seconds: None,
        )
        guard.record(identity.pid, timeout=0)
        return guard, spec, inspector

    def test_transient_post_term_inspection_error_is_retried_without_kill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            guard, spec, inspector = self.make_guard(
                Path(directory), [RuntimeError("transient inspection failure"), None]
            )

            guard.stop()

            self.assertEqual(inspector.signals, [signal.SIGTERM])
            self.assertFalse(spec.pid_file.exists())
            self.assertFalse(spec.identity_file.exists())

    def test_persistent_post_term_inspection_error_fails_closed_without_kill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            guard, spec, inspector = self.make_guard(
                Path(directory),
                [
                    RuntimeError("inspection unavailable"),
                    RuntimeError("inspection still unavailable"),
                ],
            )

            with self.assertRaisesRegex(ProcessGuardError, "cannot verify"):
                guard.stop()

            self.assertEqual(inspector.signals, [signal.SIGTERM])
            self.assertTrue(spec.pid_file.exists())
            self.assertTrue(spec.identity_file.exists())


class RpcRuntimeShellSafetyTests(unittest.TestCase):
    def test_runtime_delegates_all_signals_and_hardens_private_files(self) -> None:
        source = RUNTIME_SCRIPT.read_text(encoding="utf-8")
        for forbidden in (
            "kill -0",
            "kill ",
            "killall",
            "pkill",
            "pgrep",
            "/proc/$pid",
            "cmdline",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("umask 077", source)
        self.assertIn('chmod 700 "$RUN_DIR"', source)
        self.assertIn("cluster.infrastructure.process_guard", source)
        self.assertIn("--argv-json", source)
        self.assertIn("--from-record", source)
        self.assertIn("guard_call record", source)
        self.assertIn("guard_call status", source)
        self.assertIn("guard_call stop", source)
        self.assertIn("guard_call terminate-candidate", source)
        self.assertIn("flock -w 10", source)
        self.assertIn('prepare_private_file "$log_file"', source)
        self.assertIn(".identity.json", source)
        self.assertIn("ss -ltnpH", source)
        self.assertIn('"pid=$candidate_pid,"', source)
        self.assertIn("arm_candidate_rollback", source)
        self.assertIn("disarm_candidate_rollback", source)
        self.assertIn('trap \'rpc_exit_cleanup $?\' EXIT', source)
        self.assertEqual(
            source.count('"${command[@]}" >"$log_file" 2>&1 9>&- &'),
            2,
        )
        self.assertLess(source.index('cd "$PROJECT_ROOT"'), source.index("start_worker()"))

    def test_empty_stop_creates_only_private_runtime_and_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "cluster" / "rpc" / "runtime.sh"
            script.parent.mkdir(parents=True)
            shutil.copy2(RUNTIME_SCRIPT, script)
            script.chmod(0o755)
            python_link = root / ".venv" / "bin" / "python"
            python_link.parent.mkdir(parents=True)
            python_link.symlink_to(Path(sys.executable).resolve())
            fake_bin = root / "test-bin"
            fake_bin.mkdir()
            fake_flock = fake_bin / "flock"
            fake_flock.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_flock.chmod(0o755)
            environment = dict(os.environ)
            environment["PATH"] = f"{fake_bin}:{environment.get('PATH', '')}"

            completed = subprocess.run(
                [str(script), "stop-worker", "50052"],
                cwd=root,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            runtime = root / ".run" / "cluster"
            lock = runtime / "rpc_worker_50052.lock"
            self.assertEqual(mode(runtime), 0o700)
            self.assertEqual(mode(lock), 0o600)
            self.assertFalse((runtime / "rpc_worker_50052.pid").exists())
            self.assertFalse((runtime / "rpc_worker_50052.identity.json").exists())

    def test_runtime_shell_syntax(self) -> None:
        completed = subprocess.run(
            ["bash", "-n", str(RUNTIME_SCRIPT)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()

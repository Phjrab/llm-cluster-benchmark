"""Offline packaging and role-dependency contract tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from email.parser import BytesParser
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility for the declared floor.
    from pip._vendor import tomli as tomllib


ROOT = Path(__file__).resolve().parents[2]


def _requirement_lines(path: Path) -> list[str]:
    """Resolve the repository's simple pinned requirement includes."""
    requirements: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-r ") or line.startswith("--requirement "):
            include = line.split(maxsplit=1)[1]
            requirements.extend(_requirement_lines(ROOT / include))
            continue
        requirements.append(line)
    return requirements


def _normalized_name(requirement: str) -> str:
    name = requirement
    for separator in ("===", "==", ">=", "<=", "~=", "!=", ">", "<", "["):
        name = name.split(separator, 1)[0]
    return name.strip().lower().replace("_", "-")


def _packaging_python() -> str | None:
    """Find a local interpreter with the declared offline build backend."""
    candidates = [sys.executable, shutil.which("python3")]
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        executable = str(Path(candidate).absolute())
        if executable in seen:
            continue
        seen.add(executable)
        completed = subprocess.run(
            [executable, "-c", "import setuptools, wheel"],
            cwd=tempfile.gettempdir(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            return executable
    return None


class PackagingMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    def test_metadata_and_role_extras_match_pinned_setup_requirements(self) -> None:
        project = self.metadata["project"]
        self.assertEqual(project["requires-python"], ">=3.10")
        self.assertEqual(project.get("dependencies"), [])

        extras = project["optional-dependencies"]
        controller = _requirement_lines(ROOT / "requirements-controller.txt")
        worker = _requirement_lines(ROOT / "requirements-worker.txt")
        self.assertCountEqual(extras["controller"], controller)
        self.assertCountEqual(extras["worker"], worker)

        forbidden_controller_packages = {
            "llama-cpp-python",
            "torch",
            "torchvision",
            "jetson-stats",
            "jtop",
            "huggingface-hub",
        }
        self.assertTrue(
            forbidden_controller_packages.isdisjoint(
                {_normalized_name(requirement) for requirement in extras["controller"]}
            )
        )

    def test_package_discovery_and_data_are_explicit(self) -> None:
        setuptools_config = self.metadata["tool"]["setuptools"]
        discovery = setuptools_config["packages"]["find"]
        self.assertEqual(discovery["include"], ["cluster", "cluster.*"])
        self.assertEqual(discovery["exclude"], ["cluster.tests", "cluster.tests.*"])
        self.assertFalse(discovery["namespaces"])

        package_data = set(setuptools_config["package-data"]["cluster"])
        self.assertTrue(
            {
                "requirements-runtime.txt",
                "config/*.json",
                "config/*.csv",
                "dashboard/static/js/*.js",
                "dashboard/templates/*.html",
                "rpc/*.sh",
                "worker/*.sh",
            }.issubset(package_data)
        )


class WheelInstallationTests(unittest.TestCase):
    def test_wheel_contains_runtime_data_and_imports_outside_source_tree(self) -> None:
        packaging_python = _packaging_python()
        if packaging_python is None:
            self.skipTest("no local Python interpreter has setuptools and wheel for an offline build")

        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            source = temp_root / "source"
            source.mkdir()
            for filename in (
                "pyproject.toml",
                "README.md",
                "requirements-controller.txt",
                "requirements-worker.txt",
            ):
                shutil.copy2(ROOT / filename, source / filename)
            shutil.copytree(
                ROOT / "cluster",
                source / "cluster",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )

            wheel_dir = temp_root / "wheelhouse"
            wheel_dir.mkdir()
            environment = {
                **os.environ,
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PIP_NO_INDEX": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            built = subprocess.run(
                [
                    packaging_python,
                    "-m",
                    "pip",
                    "wheel",
                    "--no-build-isolation",
                    "--no-deps",
                    "--wheel-dir",
                    str(wheel_dir),
                    str(source),
                ],
                cwd=temp_root,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            wheels = list(wheel_dir.glob("*.whl"))
            self.assertEqual(len(wheels), 1, wheels)
            wheel_path = wheels[0]

            with zipfile.ZipFile(wheel_path) as archive:
                names = set(archive.namelist())
                expected_package_files = {
                    "cluster/__init__.py",
                    "cluster/domain/errors.py",
                    "cluster/config/experiment_defaults.json",
                    "cluster/config/model_catalog.json",
                    "cluster/config/nodes.example.csv",
                    "cluster/dashboard/templates/index.html",
                    "cluster/dashboard/static/app.js",
                    "cluster/dashboard/static/js/results.js",
                    "cluster/requirements-runtime.txt",
                    "cluster/rpc/runtime.sh",
                    "cluster/worker/start.sh",
                    "cluster/worker/stop.sh",
                    "cluster/worker_setup.sh",
                }
                self.assertTrue(expected_package_files.issubset(names), sorted(expected_package_files - names))
                self.assertFalse(any(name.startswith("cluster/tests/") for name in names))
                self.assertFalse(any(".run/" in name or name.endswith(".gguf") for name in names))
                self.assertTrue(
                    any(
                        name.endswith("share/llm-cluster-benchmark/requirements-controller.txt")
                        for name in names
                    )
                )
                self.assertTrue(
                    any(
                        name.endswith("share/llm-cluster-benchmark/requirements-worker.txt")
                        for name in names
                    )
                )

                metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
                metadata = BytesParser().parsebytes(archive.read(metadata_name))
                self.assertEqual(metadata["Requires-Python"], ">=3.10")
                self.assertCountEqual(metadata.get_all("Provides-Extra"), ["controller", "worker"])
                self.assertTrue(
                    all("extra ==" in requirement for requirement in metadata.get_all("Requires-Dist"))
                )

            environment_dir = temp_root / "installed"
            created = subprocess.run(
                [packaging_python, "-m", "venv", str(environment_dir)],
                cwd=temp_root,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            installed_python = (
                environment_dir / "Scripts" / "python.exe"
                if os.name == "nt"
                else environment_dir / "bin" / "python"
            )
            installed = subprocess.run(
                [
                    str(installed_python),
                    "-m",
                    "pip",
                    "install",
                    "--no-index",
                    "--no-deps",
                    str(wheel_path),
                ],
                cwd=temp_root,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)

            clean_environment = {**environment, "PYTHONPATH": ""}
            imported = subprocess.run(
                [
                    str(installed_python),
                    "-I",
                    "-c",
                    (
                        "import cluster, cluster.domain.errors, cluster.benchmark.metrics; "
                        "import cluster.integrations.runtime_layout, cluster.worker.inference; "
                        "from importlib.resources import files; "
                        "catalog = files('cluster').joinpath('config', 'model_catalog.json'); "
                        "assert catalog.is_file(); "
                        "print(cluster.__file__); print(catalog)"
                    ),
                ],
                cwd=temp_root,
                env=clean_environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertEqual(imported.returncode, 0, imported.stdout + imported.stderr)
            self.assertIn(str(environment_dir), imported.stdout)
            self.assertNotIn(str(ROOT), imported.stdout)


if __name__ == "__main__":
    unittest.main()

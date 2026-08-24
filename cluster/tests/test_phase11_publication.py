"""Roadmap Phase 11 deterministic statistics and publication bundle gates."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

from cluster.research.publication import (
    PublicationError,
    describe,
    load_result_dataset,
    write_publication_bundle,
)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def create_run(
    root: Path,
    run_id: str,
    *,
    experiment_type: str = "pilot",
    value: float = 10.0,
    status: str = "completed",
    campaign_identity: bool = True,
) -> None:
    directory = root / run_id
    identity = {
        "experiment_type": experiment_type,
        "pilot_id": "pilot-fixture" if experiment_type == "pilot" else "",
        "pilot_cell_id": "pilot-cell-a" if experiment_type == "pilot" else "",
        "pilot_repeat_index": int(run_id[-1]),
        "pilot_order_index": int(run_id[-1]),
        "campaign_id": "formal-fixture" if experiment_type == "formal" and campaign_identity else "",
        "campaign_cell_id": f"formal-cell-a--repeat-0{run_id[-1]}" if experiment_type == "formal" else "",
        "campaign_attempt_id": f"attempt-{run_id}" if experiment_type == "formal" else "",
        "repeat_index": int(run_id[-1]) if experiment_type == "formal" else 0,
        "order_index": int(run_id[-1]) if experiment_type == "formal" else 0,
        "prompt_id": "general-ko-001",
    }
    config = {
        "experiment_type": experiment_type,
        "model_id": "models/model.gguf",
        "node_names": ["pi-worker-02"],
        "execution_strategy": "single_node",
        "prompt": "secret prompt must not enter publication tables",
        **identity,
    }
    summary = {
        "schema_version": 2,
        "run_id": run_id,
        "status": status,
        "model_id": "models/model.gguf",
        "execution_strategy": "single_node",
        "nodes": ["pi-worker-02"],
        "participant_nodes": [{"name": "pi-worker-02", "detected_platform": "raspberry-pi"}],
        "cluster_tokens_per_s": value,
        "requests_per_s": value / 100,
        "ttft_p50_s": value / 10,
        "e2e_p50_s": value / 5,
        "success_rate": 0.9,
        "successful": 9,
        "failed": 1,
        "finished_at": f"2026-08-24T00:00:0{run_id[-1]}Z",
        "measurement_quality": "clean",
        "research_identity": identity,
        "measurement_instrumentation": {
            "overall": {"generated_tokens_per_j": value / 4},
            "nodes": {"pi-worker-02": {"peak_temperature_c": 50 + value / 10}},
        },
        "benchmark_parameters": {"prompt_sha256": "a" * 64},
    }
    write_json(directory / "config.json", config)
    write_json(directory / "summary.json", summary)
    responses = []
    for request_id in (1, 2):
        responses.append(
            json.dumps(
                {
                    "request_id": request_id,
                    "logical_request_id": request_id,
                    "node": "pi-worker-02",
                    "ok": True,
                    "ttft_s": value / 10 + request_id,
                    "e2e_s": value / 5 + request_id,
                    "generated_tokens": 16,
                    "tokens_per_s": value,
                    "response": "secret model response",
                    "prompt": "secret prompt",
                },
                sort_keys=True,
            )
        )
    (directory / "responses.jsonl").write_text("\n".join(responses) + "\n", encoding="utf-8")


class StatisticsTests(unittest.TestCase):
    def test_describe_is_exact_seeded_and_uses_sample_sd(self) -> None:
        first = describe([1, 2, 3, 4], seed=7, bootstrap_resamples=200)
        second = describe([1, 2, 3, 4], seed=7, bootstrap_resamples=200)
        self.assertEqual(first, second)
        self.assertEqual(first["count"], 4)
        self.assertEqual(first["mean"], 2.5)
        self.assertEqual(first["median"], 2.5)
        self.assertAlmostEqual(first["sd"], 1.2909944487358056)
        self.assertEqual(first["iqr"], 1.5)
        self.assertEqual(first["p95"], 3.8499999999999996)
        self.assertEqual(len(first["ci95"]), 2)

    def test_empty_and_single_value_statistics_do_not_invent_variance(self) -> None:
        self.assertIsNone(describe([], seed=1, bootstrap_resamples=100)["mean"])
        single = describe([5], seed=1, bootstrap_resamples=100)
        self.assertIsNone(single["sd"])
        self.assertEqual(single["ci95"], [5.0, 5.0])


class InputSeparationTests(unittest.TestCase):
    def test_mixed_experiment_types_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_run(root, "run1", experiment_type="pilot")
            create_run(root, "run2", experiment_type="formal")
            with self.assertRaisesRegex(PublicationError, "mixing is forbidden"):
                load_result_dataset(root, experiment_type="pilot")

    def test_formal_run_requires_campaign_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_run(root, "run1", experiment_type="formal", campaign_identity=False)
            with self.assertRaisesRegex(PublicationError, "lacks campaign identity"):
                load_result_dataset(root, experiment_type="formal")

    def test_smoke_is_not_an_accepted_publication_class(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(PublicationError, "formal or pilot"):
                load_result_dataset(Path(temp), experiment_type="smoke")


class PublicationBundleTests(unittest.TestCase):
    def plan(self) -> dict:
        return {"confidence_intervals": {"seed": 20260823, "bootstrap_resamples": 200}}

    def locked_inputs(self, directory: Path) -> dict[str, Path]:
        values = {}
        for name in ("analysis-plan.json", "matrix.json", "model-lock.json"):
            path = directory / name
            write_json(path, {"name": name, "version": 1})
            values[name] = path
        return values

    def test_pilot_bundle_requires_explicit_non_formal_acknowledgement(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            results = root / "results"
            create_run(results, "run1")
            with self.assertRaisesRegex(PublicationError, "explicit acknowledgement"):
                write_publication_bundle(
                    results_root=results,
                    output_dir=root / "bundle",
                    experiment_type="pilot",
                    analysis_plan=self.plan(),
                    locked_inputs=self.locked_inputs(root / "locks"),
                )

    def test_bundle_is_deterministic_private_and_omits_prompt_and_response_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            results = root / "results"
            create_run(results, "run1", value=10)
            create_run(results, "run2", value=14)
            locks = self.locked_inputs(root / "locks")
            first = write_publication_bundle(
                results_root=results,
                output_dir=root / "bundle-a",
                experiment_type="pilot",
                analysis_plan=self.plan(),
                locked_inputs=locks,
                acknowledge_non_formal=True,
            )
            second = write_publication_bundle(
                results_root=results,
                output_dir=root / "bundle-b",
                experiment_type="pilot",
                analysis_plan=self.plan(),
                locked_inputs=locks,
                acknowledge_non_formal=True,
            )
            self.assertEqual(first["archive_sha256"], second["archive_sha256"])
            self.assertEqual((root / "bundle-a.zip").read_bytes(), (root / "bundle-b.zip").read_bytes())
            self.assertEqual(os.stat(root / "bundle-a").st_mode & 0o777, 0o700)
            for path in (root / "bundle-a").rglob("*"):
                if path.is_file():
                    self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            payload = b"".join(path.read_bytes() for path in (root / "bundle-a").rglob("*") if path.is_file())
            self.assertNotIn(b"secret prompt", payload)
            self.assertNotIn(b"secret model response", payload)
            self.assertIn(b"NON-FORMAL PILOT OUTPUT", (root / "bundle-a" / "README.md").read_bytes())
            self.assertEqual(
                {path.name for path in (root / "bundle-a" / "figures").iterdir()},
                {"throughput.svg", "latency-ecdf.svg", "energy-efficiency.svg", "peak-temperature.svg", "run-outcomes.svg"},
            )
            with zipfile.ZipFile(root / "bundle-a.zip") as archive:
                self.assertIn("bundle-manifest.json", archive.namelist())
                self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist()))

    def test_output_is_immutable_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            results = root / "results"
            create_run(results, "run1")
            output = root / "bundle"
            output.mkdir()
            with self.assertRaisesRegex(PublicationError, "already exists"):
                write_publication_bundle(
                    results_root=results,
                    output_dir=output,
                    experiment_type="pilot",
                    analysis_plan=self.plan(),
                    locked_inputs=self.locked_inputs(root / "locks"),
                    acknowledge_non_formal=True,
                )


if __name__ == "__main__":
    unittest.main()

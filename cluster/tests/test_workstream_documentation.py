"""Regression contract for the WS-08 operator/research documentation."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs" / "operations-and-research-guide.md"


class WorkstreamDocumentationTests(unittest.TestCase):
    def test_quick_start_names_real_scripts_and_launcher_actions(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        launcher = (ROOT / "scripts" / "llm-cluster").read_text(encoding="utf-8")
        self.assertTrue((ROOT / "scripts" / "setup-controller").is_file())
        for command in (
            "./scripts/setup-controller",
            "llm-cluster start",
            "llm-cluster status",
            "llm-cluster logs",
            "llm-cluster restart",
            "llm-cluster stop",
        ):
            self.assertIn(command, readme)
        for action in ("start", "stop", "restart", "status", "logs"):
            self.assertIn(action, launcher)

    def test_guide_covers_workstream_acceptance_contract(self) -> None:
        guide = GUIDE.read_text(encoding="utf-8")
        required = (
            "Controller", "Worker", "RFC1918", "single Worker inference slot",
            "broadcast_compare", "model_parallel_rpc", "logical_requests_per_s",
            "physical_requests_per_s", "effective_user_tokens_per_s",
            "physical_cluster_tokens_per_s", "inference_slots=1",
            "model_load_distribution_s", "rpc-cleanup-check", "Jetson",
            "Raspberry Pi", "history_warning", "active_degraded", "Smoke",
            "Pilot", "Formal/publication-quality", "persist_prompt=false",
            "responses.jsonl", "measurements.jsonl", "schema_version",
            "Formal lock review and approval", "exploratory",
        )
        for marker in required:
            with self.subTest(marker=marker):
                self.assertIn(marker, guide)

    def test_result_tables_pair_formulas_with_interpretation_cautions(self) -> None:
        guide = GUIDE.read_text(encoding="utf-8")
        self.assertGreaterEqual(guide.count("| 결과 필드 | 계산 | 해석 주의 |"), 2)
        formulas = (
            "`successful / wall_s`",
            "`all_success / wall_s`",
            "모든 성공 물리 응답의 생성 token 합 / `wall_s`",
            "`energy_j / successful physical requests`",
            "successful generated tokens / `energy_j`",
            "`speedup_vs_baseline / node_count`",
        )
        for formula in formulas:
            with self.subTest(formula=formula):
                self.assertIn(formula, guide)
        self.assertIn("결측치는 0이 아니며", guide)
        self.assertIn("사용자 token/s로 표기하면 안 된다", guide)

    def test_internal_markdown_links_resolve(self) -> None:
        for document in (ROOT / "README.md", ROOT / "cluster" / "README.md", GUIDE):
            text = document.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
                if "://" in target or target.startswith("#"):
                    continue
                resolved = (document.parent / target.split("#", 1)[0]).resolve()
                with self.subTest(document=document.name, target=target):
                    self.assertTrue(resolved.exists(), resolved)

    def test_workstream_report_preserves_phase_document_names(self) -> None:
        report = (ROOT / "docs" / "workstreams" / "ws-08-operations-research-docs.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("phase/refactor documents were not renamed", report)
        self.assertTrue(any((ROOT / "docs" / "refactor").glob("phase-*.md")))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cluster.benchmark.runner import ExperimentConfig, percentile
from cluster.clusterctl import load_nodes, select_nodes


INVENTORY = """name,role,host,user,ssh_port,api_port,project_dir,enabled,identity_file
jetson-head,head,127.0.0.1,jetson,22,8000,/opt/llm,true,
jetson-worker-01,worker,192.168.0.27,jetson,22,8000,/opt/llm,true,
jetson-worker-02,worker,192.168.0.28,jetson,22,8000,/opt/llm,false,
"""


class InventoryTests(unittest.TestCase):
    def test_loads_enabled_nodes_and_selects_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nodes.csv"
            path.write_text(INVENTORY, encoding="utf-8")
            nodes = load_nodes(path)
            self.assertEqual([node.name for node in nodes], ["jetson-head", "jetson-worker-01"])
            selected = select_nodes(nodes, ["jetson-worker-01"])
            self.assertEqual(selected[0].role, "worker")

    def test_rejects_inventory_without_one_enabled_head(self) -> None:
        invalid = INVENTORY.replace("jetson-head,head,127.0.0.1,jetson,22,8000,/opt/llm,true", "jetson-head,head,127.0.0.1,jetson,22,8000,/opt/llm,false")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nodes.csv"
            path.write_text(invalid, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exactly one enabled head"):
                load_nodes(path)


class ExperimentTests(unittest.TestCase):
    def test_validates_reproducible_config(self) -> None:
        config = ExperimentConfig(node_names=["jetson-head"])
        config.validate()

    def test_rejects_unsafe_model_path(self) -> None:
        config = ExperimentConfig(node_names=["jetson-head"], model_id="../model.gguf")
        with self.assertRaisesRegex(ValueError, "safe relative"):
            config.validate()

    def test_percentile_interpolates(self) -> None:
        self.assertEqual(percentile([1.0, 2.0, 3.0, 4.0], 0.50), 2.5)
        self.assertAlmostEqual(percentile([1.0, 2.0, 3.0, 4.0], 0.95), 3.85)


if __name__ == "__main__":
    unittest.main()

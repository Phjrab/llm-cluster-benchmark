"""Regression contracts for Controller-managed Jetson nvpmodel selection."""

from __future__ import annotations

import argparse
import json
import unittest
from unittest import mock

from cluster.clusterctl import Node, command_power_set
from cluster.worker import power_control


MODES = """
POWER_MODEL: ID=0 NAME=10W
POWER_MODEL: ID=2 NAME=MAXN_SUPER
POWER_MODEL: ID=3 NAME=25W
"""


class WorkerPowerControlTests(unittest.TestCase):
    def test_parse_modes_and_unambiguous_maximum_candidate(self) -> None:
        modes = power_control.parse_modes(MODES)
        self.assertEqual([(item["id"], item["name"]) for item in modes], [(0, "10W"), (2, "MAXN_SUPER"), (3, "25W")])
        candidate = power_control._recommended_mode(modes)
        self.assertEqual(candidate["id"], 3)
        self.assertEqual(candidate["power_budget_w"], 25.0)

    def test_multiple_equal_maximum_modes_do_not_silently_choose_one(self) -> None:
        modes = power_control.parse_modes("POWER_MODEL: ID=0 NAME=MAXN_25W\nPOWER_MODEL: ID=2 NAME=MAXN_SUPER_25W\n")
        self.assertIsNone(power_control._recommended_mode(modes))

    def test_set_rejects_a_mode_not_advertised_by_the_same_jetson(self) -> None:
        report = {
            "ok": True,
            "supported": True,
            "modes": [{"id": 2, "name": "15W", "power_budget_w": 15.0}],
            "can_apply": True,
        }
        with mock.patch.object(power_control, "status", return_value=report), mock.patch.object(power_control, "_run") as invoked:
            result, code = power_control.set_mode(3)
        self.assertEqual(code, 2)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "invalid_mode_id")
        invoked.assert_not_called()


class ControllerPowerCommandTests(unittest.TestCase):
    @staticmethod
    def node(name: str = "jetson-01") -> Node:
        return Node(
            name=name,
            role="worker",
            host="192.168.0.26",
            user="jetson",
            ssh_port=22,
            api_port=8000,
            project_dir="/home/jetson/llm-cluster-benchmark-worker",
            enabled=True,
            platform="jetson",
        )

    def test_set_requires_one_explicit_target_and_confirmation(self) -> None:
        args = argparse.Namespace(mode_id=2, confirmed=True)
        self.assertEqual(command_power_set([], args), 2)
        self.assertEqual(command_power_set([self.node(), self.node("jetson-02")], args), 2)
        self.assertEqual(command_power_set([self.node()], argparse.Namespace(mode_id=2, confirmed=False)), 2)

    def test_set_propagates_only_validated_integer_to_remote_helper(self) -> None:
        args = argparse.Namespace(mode_id=2, confirmed=True)
        with mock.patch("cluster.clusterctl._power_control_one", return_value={"name": "jetson-01", "ok": True, "power": {}}) as controlled, mock.patch("sys.stdout") as output:
            result = command_power_set([self.node()], args)
        self.assertEqual(result, 0)
        controlled.assert_called_once_with(self.node(), "set", 2)
        self.assertIn('"ok": true', "".join(call.args[0] for call in output.write.call_args_list))


if __name__ == "__main__":
    unittest.main()

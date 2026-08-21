from __future__ import annotations

import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from cluster.domain.power import (
    MeasurementQuality,
    PowerIntegrityStatus,
    PowerWarningCode,
    decode_get_throttled,
    decode_throttled_mask,
    parse_get_throttled_output,
)
from cluster.worker.telemetry import GenericPsutilTelemetry, RaspberryPiTelemetry


FIXED_TIME = "2026-08-21T00:00:00+00:00"


class RaspberryPiPowerDomainTests(unittest.TestCase):
    def test_zero_mask_is_clean_and_non_blocking(self) -> None:
        record = decode_get_throttled("throttled=0x0", observed_at=FIXED_TIME)
        self.assertTrue(record.available)
        self.assertEqual(record.status, PowerIntegrityStatus.OK)
        self.assertFalse(record.blocking)
        self.assertEqual(record.reason_codes, ())
        self.assertEqual(record.raw_hex, "0x0")

    def test_50000_is_history_only_not_current_degradation(self) -> None:
        record = decode_get_throttled("throttled=0x50000", observed_at=FIXED_TIME)
        self.assertTrue(record.available)
        self.assertEqual(record.status, PowerIntegrityStatus.HISTORY_WARNING)
        self.assertFalse(record.blocking)
        self.assertFalse(record.current.undervoltage)
        self.assertFalse(record.current.frequency_capped)
        self.assertFalse(record.current.throttled)
        self.assertFalse(record.current.soft_temperature_limit)
        self.assertTrue(record.history.undervoltage)
        self.assertFalse(record.history.frequency_capped)
        self.assertTrue(record.history.throttled)
        self.assertFalse(record.history.soft_temperature_limit)
        self.assertEqual(
            record.reason_codes,
            (
                PowerWarningCode.PI_UNDERVOLTAGE_HISTORY,
                PowerWarningCode.PI_THROTTLING_HISTORY,
            ),
        )
        self.assertIn("Historical", record.message)

    def test_parser_accepts_documented_case_and_whitespace_variants(self) -> None:
        for output in ("0x50000", "  THROTTLED=0X50000\n", "\tthrottled = 0x50000  "):
            self.assertEqual(parse_get_throttled_output(output), 0x50000)

    def test_parser_rejects_malformed_negative_extra_and_out_of_range_values(self) -> None:
        for output in ("", "throttled=", "-1", "0x1 extra", "not-a-mask", "0x100000000"):
            with self.subTest(output=output):
                with self.assertRaises(ValueError):
                    parse_get_throttled_output(output)

    def test_current_bits_take_precedence_and_reason_order_is_stable(self) -> None:
        record = decode_throttled_mask(0x1 | 0x4 | 0x10000 | 0x40000, observed_at=FIXED_TIME)
        self.assertEqual(record.status, PowerIntegrityStatus.ACTIVE_DEGRADED)
        self.assertEqual(
            record.reason_codes,
            (
                PowerWarningCode.PI_UNDERVOLTAGE_ACTIVE,
                PowerWarningCode.PI_THROTTLING_ACTIVE,
                PowerWarningCode.PI_UNDERVOLTAGE_HISTORY,
                PowerWarningCode.PI_THROTTLING_HISTORY,
            ),
        )

    def test_each_known_bit_is_decoded(self) -> None:
        bits = (
            (0x1, "current", "undervoltage"),
            (0x2, "current", "frequency_capped"),
            (0x4, "current", "throttled"),
            (0x8, "current", "soft_temperature_limit"),
            (0x10000, "history", "undervoltage"),
            (0x20000, "history", "frequency_capped"),
            (0x40000, "history", "throttled"),
            (0x80000, "history", "soft_temperature_limit"),
        )
        for value, group, name in bits:
            with self.subTest(value=value):
                record = decode_throttled_mask(value, observed_at=FIXED_TIME)
                self.assertTrue(getattr(getattr(record, group), name))

    def test_unknown_bits_are_not_silently_clean(self) -> None:
        record = decode_throttled_mask(0x10, observed_at=FIXED_TIME)
        self.assertEqual(record.status, PowerIntegrityStatus.HISTORY_WARNING)
        self.assertEqual(record.unknown_bits, 0x10)
        self.assertEqual(record.reason_codes, (PowerWarningCode.PI_POWER_UNKNOWN_BITS,))

    def test_measurement_quality_is_only_a_type_in_this_phase(self) -> None:
        self.assertEqual(MeasurementQuality.UNKNOWN.value, "unknown")


class RaspberryPiPowerProbeTests(unittest.TestCase):
    def make_provider(self, runner):
        return RaspberryPiTelemetry(
            Path(tempfile.gettempdir()),
            power_command_runner=runner,
            clock=lambda: datetime(2026, 8, 21, tzinfo=timezone.utc),
        )

    def test_fixed_argv_probe_serializes_history_warning(self) -> None:
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout="throttled=0x50000\n", stderr="")

        payload = self.make_provider(runner).power_integrity()
        self.assertEqual(calls[0][0], ["vcgencmd", "get_throttled"])
        self.assertEqual(calls[0][1]["timeout"], 2)
        self.assertTrue(calls[0][1]["capture_output"])
        self.assertFalse(calls[0][1]["check"])
        self.assertFalse(calls[0][1]["shell"])
        self.assertEqual(payload["status"], "history_warning")
        self.assertFalse(payload["blocking"])
        self.assertFalse(payload["current"]["undervoltage"])
        self.assertTrue(payload["history"]["undervoltage"])
        self.assertTrue(payload["history"]["throttled"])

    def test_probe_failures_are_unavailable_without_stderr_leakage(self) -> None:
        failures = (
            lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("vcgencmd /secret/path")),
            lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 1, stdout="", stderr="/secret/stderr"),
            lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("vcgencmd", 2)),
            lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, stdout="bad output", stderr="/secret/stderr"),
        )
        for runner in failures:
            with self.subTest(runner=runner):
                payload = self.make_provider(runner).power_integrity()
                self.assertFalse(payload["available"])
                self.assertEqual(payload["status"], "unavailable")
                self.assertFalse(payload["blocking"])
                self.assertEqual(payload["reason_codes"], ["PI_POWER_STATUS_UNAVAILABLE"])
                self.assertNotIn("/secret", payload["message"])

    def test_non_pi_provider_never_has_a_vcgencmd_power_probe(self) -> None:
        self.assertIsNone(GenericPsutilTelemetry(Path(tempfile.gettempdir())).power_integrity())


if __name__ == "__main__":
    unittest.main()

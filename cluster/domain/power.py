"""Pure Raspberry Pi power-integrity decoding.

This module deliberately contains no subprocess, telemetry, or Worker API
dependency. A power observation is research context, never an inference or
experiment-admission failure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Tuple


CURRENT_UNDERVOLTAGE = 1 << 0
CURRENT_FREQUENCY_CAPPED = 1 << 1
CURRENT_THROTTLED = 1 << 2
CURRENT_SOFT_TEMP_LIMIT = 1 << 3
HISTORY_UNDERVOLTAGE = 1 << 16
HISTORY_FREQUENCY_CAPPED = 1 << 17
HISTORY_THROTTLED = 1 << 18
HISTORY_SOFT_TEMP_LIMIT = 1 << 19
KNOWN_MASK = (
    CURRENT_UNDERVOLTAGE | CURRENT_FREQUENCY_CAPPED | CURRENT_THROTTLED
    | CURRENT_SOFT_TEMP_LIMIT | HISTORY_UNDERVOLTAGE | HISTORY_FREQUENCY_CAPPED
    | HISTORY_THROTTLED | HISTORY_SOFT_TEMP_LIMIT
)
MAX_THROTTLED_MASK = 0xFFFFFFFF
_THROTTLED_OUTPUT = re.compile(r"^\s*(?:throttled\s*=\s*)?(0x[0-9a-f]+)\s*$", re.IGNORECASE)


class PowerIntegrityStatus(str, Enum):
    OK = "ok"
    HISTORY_WARNING = "history_warning"
    ACTIVE_DEGRADED = "active_degraded"
    UNAVAILABLE = "unavailable"

    def __str__(self) -> str:
        return self.value


class MeasurementQuality(str, Enum):
    """Run-level type reserved for Follow-up 02 aggregation."""

    CLEAN = "clean"
    WARNING = "warning"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        return self.value


class PowerWarningCode(str, Enum):
    PI_UNDERVOLTAGE_HISTORY = "PI_UNDERVOLTAGE_HISTORY"
    PI_FREQUENCY_CAPPED_HISTORY = "PI_FREQUENCY_CAPPED_HISTORY"
    PI_THROTTLING_HISTORY = "PI_THROTTLING_HISTORY"
    PI_SOFT_TEMP_LIMIT_HISTORY = "PI_SOFT_TEMP_LIMIT_HISTORY"
    PI_UNDERVOLTAGE_ACTIVE = "PI_UNDERVOLTAGE_ACTIVE"
    PI_FREQUENCY_CAPPED_ACTIVE = "PI_FREQUENCY_CAPPED_ACTIVE"
    PI_THROTTLING_ACTIVE = "PI_THROTTLING_ACTIVE"
    PI_SOFT_TEMP_LIMIT_ACTIVE = "PI_SOFT_TEMP_LIMIT_ACTIVE"
    PI_POWER_UNKNOWN_BITS = "PI_POWER_UNKNOWN_BITS"
    PI_POWER_STATUS_UNAVAILABLE = "PI_POWER_STATUS_UNAVAILABLE"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class PowerConditionBits:
    undervoltage: bool = False
    frequency_capped: bool = False
    throttled: bool = False
    soft_temperature_limit: bool = False

    def to_dict(self) -> Dict[str, bool]:
        return {
            "undervoltage": self.undervoltage,
            "frequency_capped": self.frequency_capped,
            "throttled": self.throttled,
            "soft_temperature_limit": self.soft_temperature_limit,
        }


@dataclass(frozen=True)
class RaspberryPiPowerIntegrity:
    available: bool
    status: PowerIntegrityStatus
    blocking: bool
    source: str
    raw_value: int | None
    raw_hex: str | None
    current: PowerConditionBits
    history: PowerConditionBits
    reason_codes: Tuple[PowerWarningCode, ...]
    observed_at: str
    message: str
    unknown_bits: int = 0

    def __post_init__(self) -> None:
        if self.blocking:
            object.__setattr__(self, "blocking", False)

    def to_dict(self) -> Dict[str, object]:
        return {
            "available": self.available,
            "status": self.status.value,
            "blocking": False,
            "source": self.source,
            "raw_value": self.raw_value,
            "raw_hex": self.raw_hex,
            "current": self.current.to_dict(),
            "history": self.history.to_dict(),
            "reason_codes": [code.value for code in self.reason_codes],
            "observed_at": self.observed_at,
            "message": self.message,
            "unknown_bits": self.unknown_bits,
        }


def parse_get_throttled_output(output: str) -> int:
    """Parse the documented vcgencmd get_throttled output shape."""
    if not isinstance(output, str):
        raise ValueError("get_throttled output must be text")
    match = _THROTTLED_OUTPUT.fullmatch(output)
    if match is None:
        raise ValueError("invalid vcgencmd get_throttled output")
    value = int(match.group(1), 16)
    if value > MAX_THROTTLED_MASK:
        raise ValueError("get_throttled value is outside the supported range")
    return value


def _bits(value: int, *, shift: int = 0) -> PowerConditionBits:
    return PowerConditionBits(
        undervoltage=bool(value & (CURRENT_UNDERVOLTAGE << shift)),
        frequency_capped=bool(value & (CURRENT_FREQUENCY_CAPPED << shift)),
        throttled=bool(value & (CURRENT_THROTTLED << shift)),
        soft_temperature_limit=bool(value & (CURRENT_SOFT_TEMP_LIMIT << shift)),
    )


def _reason_codes(
    current: PowerConditionBits, history: PowerConditionBits, unknown_bits: int
) -> Tuple[PowerWarningCode, ...]:
    values = []
    for enabled, code in (
        (current.undervoltage, PowerWarningCode.PI_UNDERVOLTAGE_ACTIVE),
        (current.frequency_capped, PowerWarningCode.PI_FREQUENCY_CAPPED_ACTIVE),
        (current.throttled, PowerWarningCode.PI_THROTTLING_ACTIVE),
        (current.soft_temperature_limit, PowerWarningCode.PI_SOFT_TEMP_LIMIT_ACTIVE),
        (history.undervoltage, PowerWarningCode.PI_UNDERVOLTAGE_HISTORY),
        (history.frequency_capped, PowerWarningCode.PI_FREQUENCY_CAPPED_HISTORY),
        (history.throttled, PowerWarningCode.PI_THROTTLING_HISTORY),
        (history.soft_temperature_limit, PowerWarningCode.PI_SOFT_TEMP_LIMIT_HISTORY),
    ):
        if enabled:
            values.append(code)
    if unknown_bits:
        values.append(PowerWarningCode.PI_POWER_UNKNOWN_BITS)
    return tuple(values)


def decode_throttled_mask(value: int, *, observed_at: str) -> RaspberryPiPowerIntegrity:
    """Decode a validated Raspberry Pi firmware throttling bit mask."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("get_throttled value must be an integer")
    if value < 0 or value > MAX_THROTTLED_MASK:
        raise ValueError("get_throttled value is outside the supported range")
    if not isinstance(observed_at, str) or not observed_at.strip():
        raise ValueError("observed_at is required")
    current = _bits(value)
    history = _bits(value, shift=16)
    unknown_bits = value & ~KNOWN_MASK
    reason_codes = _reason_codes(current, history, unknown_bits)
    if any(current.to_dict().values()):
        status = PowerIntegrityStatus.ACTIVE_DEGRADED
        message = "Current Raspberry Pi power or thermal condition detected."
    elif any(history.to_dict().values()):
        status = PowerIntegrityStatus.HISTORY_WARNING
        message = "Historical Raspberry Pi power or thermal condition detected."
    elif unknown_bits:
        status = PowerIntegrityStatus.HISTORY_WARNING
        message = "Unknown Raspberry Pi power-status bits detected."
    else:
        status = PowerIntegrityStatus.OK
        message = "No Raspberry Pi power integrity conditions detected."
    return RaspberryPiPowerIntegrity(
        available=True,
        status=status,
        blocking=False,
        source="vcgencmd_get_throttled",
        raw_value=value,
        raw_hex=f"0x{value:x}",
        current=current,
        history=history,
        reason_codes=reason_codes,
        observed_at=observed_at,
        message=message,
        unknown_bits=unknown_bits,
    )


def decode_get_throttled(output: str, *, observed_at: str) -> RaspberryPiPowerIntegrity:
    return decode_throttled_mask(parse_get_throttled_output(output), observed_at=observed_at)


def unavailable_power_integrity(
    *, observed_at: str, source: str = "vcgencmd_get_throttled"
) -> RaspberryPiPowerIntegrity:
    return RaspberryPiPowerIntegrity(
        available=False,
        status=PowerIntegrityStatus.UNAVAILABLE,
        blocking=False,
        source=source,
        raw_value=None,
        raw_hex=None,
        current=PowerConditionBits(),
        history=PowerConditionBits(),
        reason_codes=(PowerWarningCode.PI_POWER_STATUS_UNAVAILABLE,),
        observed_at=observed_at,
        message="Raspberry Pi power status is unavailable.",
    )


__all__ = [
    "CURRENT_FREQUENCY_CAPPED", "CURRENT_SOFT_TEMP_LIMIT", "CURRENT_THROTTLED",
    "CURRENT_UNDERVOLTAGE", "HISTORY_FREQUENCY_CAPPED", "HISTORY_SOFT_TEMP_LIMIT",
    "HISTORY_THROTTLED", "HISTORY_UNDERVOLTAGE", "KNOWN_MASK", "MeasurementQuality",
    "PowerConditionBits", "PowerIntegrityStatus", "PowerWarningCode",
    "RaspberryPiPowerIntegrity", "decode_get_throttled", "decode_throttled_mask",
    "parse_get_throttled_output", "unavailable_power_integrity",
]

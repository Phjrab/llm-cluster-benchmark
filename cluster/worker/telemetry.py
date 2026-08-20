"""Worker telemetry providers with inference-independent degradation paths."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Protocol

import psutil


def safe_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def round_optional(value: Any, digits: int = 2) -> Optional[float]:
    number = safe_float(value)
    return round(number, digits) if number is not None else None


def read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip("\x00\n ")
    except OSError:
        return ""


def temperature_snapshot(platform_kind: str) -> Dict[str, Optional[float]]:
    values: Dict[str, Optional[float]] = {}
    if platform_kind == "raspberry-pi" and shutil.which("vcgencmd"):
        try:
            output = subprocess.check_output(
                ["vcgencmd", "measure_temp"], text=True, stderr=subprocess.DEVNULL, timeout=2
            )
            match = re.search(r"(-?\d+(?:\.\d+)?)", output)
            if match:
                values["soc"] = round(float(match.group(1)), 2)
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    try:
        for group, entries in psutil.sensors_temperatures().items():
            for index, entry in enumerate(entries):
                values[f"{group}:{entry.label or f'sensor{index}'}"] = round_optional(entry.current)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        pass
    if not values:
        raw = safe_float(read_text("/sys/class/thermal/thermal_zone0/temp"))
        if raw is not None:
            values["system"] = round(raw / 1000 if raw > 1000 else raw, 2)
    return values


class TelemetryProvider(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def snapshot(self) -> Dict[str, Any]: ...

    def status(self) -> Dict[str, Any]: ...


class GenericPsutilTelemetry:
    """Portable base telemetry.  It is healthy even without vendor sensors."""

    def __init__(self, project_root: Path, platform_kind: str = "generic-linux") -> None:
        self.project_root = Path(project_root)
        self.platform_kind = platform_kind
        self._lock = threading.Lock()
        self._last_network = psutil.net_io_counters()
        self._last_network_at = time.monotonic()
        psutil.cpu_percent(interval=None, percpu=True)

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def status(self) -> Dict[str, Any]:
        return {"provider": "psutil", "ready": True, "degraded": False, "error": None}

    def _vendor_snapshot(self) -> Dict[str, Any]:
        return {}

    def snapshot(self) -> Dict[str, Any]:
        memory = psutil.virtual_memory()
        try:
            swap = psutil.swap_memory()
        except (OSError, RuntimeError):
            # Some constrained macOS/Linux environments deny the system call.
            # Telemetry must remain best-effort and never block inference.
            swap = None
        disk = psutil.disk_usage(str(self.project_root))
        cores = psutil.cpu_percent(interval=None, percpu=True)
        cpu_pct = round(sum(cores) / len(cores), 2) if cores else None
        try:
            frequency = psutil.cpu_freq()
        except (OSError, RuntimeError):
            frequency = None
        with self._lock:
            network = psutil.net_io_counters()
            now = time.monotonic()
            elapsed = max(now - self._last_network_at, 0.001)
            receive_rate = max((network.bytes_recv - self._last_network.bytes_recv) / elapsed, 0)
            send_rate = max((network.bytes_sent - self._last_network.bytes_sent) / elapsed, 0)
            self._last_network = network
            self._last_network_at = now
        temperatures = temperature_snapshot(self.platform_kind)
        load = os.getloadavg()
        try:
            uptime_s = round(time.time() - psutil.boot_time(), 1)
        except (OSError, RuntimeError, PermissionError):
            uptime_s = None
        return {
            "sampled_at": datetime.now(timezone.utc).isoformat(),
            "platform_kind": self.platform_kind,
            "cpu_pct": cpu_pct,
            "ram_pct": round(memory.percent, 2),
            "swap_pct": round(swap.percent, 2) if swap else None,
            "ram_used_mb": round(memory.used / (1024 * 1024), 2),
            "ram_available_mb": round(memory.available / (1024 * 1024), 2),
            "swap_used_mb": round(swap.used / (1024 * 1024), 2) if swap else None,
            "disk_free_gb": round(disk.free / (1024**3), 2),
            "load_1m": round(load[0], 2),
            "cpu_temp_c": next(iter(temperatures.values()), None),
            "gpu_temp_c": None,
            "gpu_pct": None,
            "power_w": None,
            "cpu": {
                "average_pct": cpu_pct,
                "cores_pct": [round(value, 2) for value in cores],
                "frequency_mhz": round_optional(frequency.current) if frequency else None,
                "frequency_min_mhz": round_optional(frequency.min) if frequency else None,
                "frequency_max_mhz": round_optional(frequency.max) if frequency else None,
                "load_1m": round(load[0], 2),
                "load_5m": round(load[1], 2),
                "load_15m": round(load[2], 2),
            },
            "memory": {
                "percent": round(memory.percent, 2),
                "used_mb": round(memory.used / (1024 * 1024), 2),
                "available_mb": round(memory.available / (1024 * 1024), 2),
                "total_mb": round(memory.total / (1024 * 1024), 2),
            },
            "swap": {
                "percent": round(swap.percent, 2) if swap else None,
                "used_mb": round(swap.used / (1024 * 1024), 2) if swap else None,
                "total_mb": round(swap.total / (1024 * 1024), 2) if swap else None,
            },
            "disk": {
                "percent": round(disk.percent, 2),
                "used_gb": round(disk.used / (1024**3), 2),
                "free_gb": round(disk.free / (1024**3), 2),
                "total_gb": round(disk.total / (1024**3), 2),
            },
            "network": {
                "bytes_received": network.bytes_recv,
                "bytes_sent": network.bytes_sent,
                "receive_bytes_s": round(receive_rate, 2),
                "send_bytes_s": round(send_rate, 2),
            },
            "temperatures_c": temperatures,
            "uptime_s": uptime_s,
            "accelerator": {"type": "cpu", "utilization_pct": None, "engines": {}},
            "power": {"total_w": None, "rails_w": {}, "mode": None, "jetson_clocks": None},
            "fans": {},
        }


class RaspberryPiTelemetry(GenericPsutilTelemetry):
    """Pi telemetry deliberately leaves unavailable GPU/power values as None."""

    def __init__(self, project_root: Path) -> None:
        super().__init__(project_root, "raspberry-pi")


class JetsonTelemetry(GenericPsutilTelemetry):
    """jtop enrichment with a psutil-only fallback when service/client disagree."""

    def __init__(
        self,
        project_root: Path,
        *,
        jtop_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        super().__init__(project_root, "jetson")
        self._jtop_factory = jtop_factory
        self._vendor: Dict[str, Any] = {}
        self._error: Optional[str] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _factory(self) -> Any:
        if self._jtop_factory is not None:
            return self._jtop_factory
        try:
            from jtop import jtop
        except ImportError as exc:
            raise RuntimeError("jtop client is not installed") from exc
        self._jtop_factory = jtop
        return jtop

    def refresh(self) -> None:
        """Collect one jtop sample; intended for deterministic tests as well."""
        try:
            factory = self._factory()
            with factory(interval=1.0) as jetson:
                if not jetson.ok():
                    raise RuntimeError("jtop service is not ready")
                stats = dict(jetson.stats)
            temperatures = {
                key.removeprefix("Temp ").lower(): round_optional(value)
                for key, value in stats.items()
                if key.startswith("Temp ")
            }
            fans = {
                key.removeprefix("Fan "): round_optional(value)
                for key, value in stats.items()
                if key.startswith("Fan ")
            }
            rails = {
                key.removeprefix("Power "): round_optional(safe_float(value) / 1000)
                for key, value in stats.items()
                if key.startswith("Power ") and key != "Power TOT" and safe_float(value) is not None
            }
            with self._lock:
                self._vendor = {
                    "gpu_pct": round_optional(stats.get("GPU")),
                    "power_w": round_optional((safe_float(stats.get("Power TOT")) or 0) / 1000)
                    if safe_float(stats.get("Power TOT")) is not None
                    else None,
                    "cpu_temp_c": temperatures.get("cpu"),
                    "gpu_temp_c": temperatures.get("gpu"),
                    "fan_pct": next(iter(fans.values()), None),
                    "power_mode": stats.get("nvp model"),
                    "jetson_clocks": stats.get("jetson_clocks"),
                    "jetson_temperatures": temperatures,
                    "jetson_fans": fans,
                    "jetson_power_rails_w": rails,
                    "jetson_engines": {
                        key: round_optional(value)
                        for key, value in stats.items()
                        if key in {"GPU", "EMC", "APE", "NVDEC", "NVENC", "NVJPG", "OFA", "SE", "VIC"}
                    },
                }
                self._error = None
        except Exception as exc:  # jtop failure never makes inference unavailable.
            with self._lock:
                self._vendor = {}
                self._error = str(exc)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.refresh()
            self._stop.wait(1.0 if self._error is None else 3.0)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="jtop-telemetry", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> Dict[str, Any]:
        with self._lock:
            active = bool(self._vendor) and self._error is None
            return {
                "provider": "jtop+psutil" if active else "psutil",
                "ready": active,
                "degraded": not active,
                "error": self._error,
            }

    def snapshot(self) -> Dict[str, Any]:
        snapshot = super().snapshot()
        with self._lock:
            vendor = dict(self._vendor)
            error = self._error
        temperatures = snapshot["temperatures_c"]
        temperatures.update({f"jetson:{key}": value for key, value in (vendor.get("jetson_temperatures") or {}).items()})
        snapshot.update(vendor)
        snapshot["cpu_temp_c"] = vendor.get("cpu_temp_c", snapshot["cpu_temp_c"])
        snapshot["gpu_temp_c"] = vendor.get("gpu_temp_c")
        snapshot["gpu_pct"] = vendor.get("gpu_pct")
        snapshot["power_w"] = vendor.get("power_w")
        snapshot["accelerator"] = {
            "type": "cuda",
            "utilization_pct": vendor.get("gpu_pct"),
            "engines": vendor.get("jetson_engines", {}),
        }
        snapshot["power"] = {
            "total_w": vendor.get("power_w"),
            "rails_w": vendor.get("jetson_power_rails_w", {}),
            "mode": vendor.get("power_mode"),
            "jetson_clocks": vendor.get("jetson_clocks"),
        }
        snapshot["fans"] = vendor.get("jetson_fans", {})
        if error:
            snapshot["sampler_error"] = error
        return snapshot


class TelemetryService:
    """Select exactly one provider; telemetry state is independent of inference."""

    def __init__(self, provider: TelemetryProvider) -> None:
        self.provider = provider

    @classmethod
    def for_platform(cls, platform_kind: str, project_root: Path) -> "TelemetryService":
        if platform_kind == "jetson":
            provider: TelemetryProvider = JetsonTelemetry(project_root)
        elif platform_kind == "raspberry-pi":
            provider = RaspberryPiTelemetry(project_root)
        else:
            provider = GenericPsutilTelemetry(project_root, platform_kind)
        return cls(provider)

    def start(self) -> None:
        self.provider.start()

    def stop(self) -> None:
        self.provider.stop()

    def snapshot(self) -> Dict[str, Any]:
        return self.provider.snapshot()

    def status(self) -> Dict[str, Any]:
        return self.provider.status()


__all__ = [
    "GenericPsutilTelemetry",
    "JetsonTelemetry",
    "RaspberryPiTelemetry",
    "TelemetryService",
    "safe_float",
]

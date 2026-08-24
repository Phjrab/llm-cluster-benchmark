"""Transport-neutral Dashboard application errors."""

from __future__ import annotations

from typing import Any


class DashboardServiceError(ValueError):
    """Application failure translated to HTTP only by route adapters."""

    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


__all__ = ["DashboardServiceError"]

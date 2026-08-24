"""Transport-neutral Dashboard application services.

The legacy :mod:`cluster.dashboard.services` module remains the compatibility
facade while responsibilities move here one at a time.
"""

from cluster.dashboard.service_layers.errors import DashboardServiceError
from cluster.dashboard.service_layers.research_service import ResearchService
from cluster.dashboard.service_layers.result_service import ResultService
from cluster.dashboard.service_layers.settings_service import SettingsService

__all__ = ["DashboardServiceError", "ResearchService", "ResultService", "SettingsService"]

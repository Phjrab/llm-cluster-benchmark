#!/usr/bin/env python3
"""FastAPI application wiring for the macOS Dashboard control plane.

Application state, storage-backed services, and Worker orchestration live in
``cluster.dashboard.services``. HTTP/Pydantic adapters live in
``cluster.dashboard.routes`` and ``cluster.dashboard.dependencies``. This
module intentionally owns only application construction and lifecycle wiring.
"""

from __future__ import annotations

import importlib
import json
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from cluster.dashboard import services as _services_module
from cluster.dashboard.routes import register_routers

# Tests and legacy launcher code reload this module after setting runtime path
# overrides. Reload the service module in place so compatibility globals are
# rebuilt from the same explicit runtime integration boundary.
_existing_experiments = getattr(_services_module, "experiments", None)
if _existing_experiments is not None:
    _existing_experiments.shutdown()
services = importlib.reload(_services_module)
LOGGER = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Build a Dashboard app without making the Controller an inference node."""
    services.ensure_runtime()

    @asynccontextmanager
    async def dashboard_lifespan(application: FastAPI):
        # JobService recovery and suite reconciliation are idempotent and stay
        # in the application service, not in request handlers.
        application.state.dashboard_services.startup()
        try:
            yield
        finally:
            application.state.dashboard_services.shutdown()

    application = FastAPI(
        title="MediFlow LLM Cluster Lab",
        version="1.0.0",
        lifespan=dashboard_lifespan,
    )
    application.state.dashboard_services = services.DashboardFacade()
    application.mount(
        "/static",
        StaticFiles(directory=str(services.DASHBOARD_DIR / "static")),
        name="cluster-static",
    )
    templates = Jinja2Templates(directory=str(services.DASHBOARD_DIR / "templates"))
    register_routers(application, templates)

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = uuid.uuid4().hex
        # Do not return or log exception text: native/runtime failures can carry
        # paths, prompts, command output, or credentials. Keep only structured
        # correlation context and the exception type.
        LOGGER.error(
            json.dumps(
                {
                    "event": "dashboard_unexpected_error",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "error_type": type(exc).__name__,
                },
                sort_keys=True,
            )
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "request_id": request_id},
            headers={"X-Request-ID": request_id},
        )

    return application


app = create_app()

# Preserve the established import surface while callers migrate to explicit
# services/repositories. These aliases contain no FastAPI routing logic.
for _name in services.COMPATIBILITY_EXPORTS:
    globals()[_name] = getattr(services, _name)

__all__ = ["app", "create_app", *services.COMPATIBILITY_EXPORTS]

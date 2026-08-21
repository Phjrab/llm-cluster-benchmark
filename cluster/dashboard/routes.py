"""Compatibility-preserving Dashboard route adapters.

Handlers only translate HTTP concerns to ``DashboardFacade`` calls.  They do
not own storage, SSH/subprocess execution, job lifecycle, or benchmark math.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from cluster.dashboard.dependencies import (
    get_dashboard_services,
    supplied_dashboard_token,
    verify_token,
)
from cluster.dashboard.schemas import (
    ActionPayload,
    ClusterSettingsPayload,
    ExperimentPayload,
    JetsonPowerModePayload,
    NodeDeletePayload,
    NodePayload,
    NodeRenamePayload,
)
from cluster.dashboard.services import DashboardFacade

web_router = APIRouter()
controller_router = APIRouter(dependencies=[Depends(verify_token)])
nodes_router = APIRouter(dependencies=[Depends(verify_token)])
environment_router = APIRouter(dependencies=[Depends(verify_token)])
actions_router = APIRouter(dependencies=[Depends(verify_token)])
models_router = APIRouter(dependencies=[Depends(verify_token)])
settings_router = APIRouter(dependencies=[Depends(verify_token)])
events_router = APIRouter(dependencies=[Depends(verify_token)])
experiments_router = APIRouter(dependencies=[Depends(verify_token)])
results_router = APIRouter(dependencies=[Depends(verify_token)])


def _error_response(error: ValueError) -> JSONResponse:
    """Render application failures without leaking a chained exception."""
    return JSONResponse(
        status_code=int(getattr(error, "status_code", 400)),
        content={"detail": getattr(error, "detail", str(error))},
    )


def register_routers(app: Any, templates: Jinja2Templates) -> None:
    @web_router.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        response = templates.TemplateResponse(request=request, name="index.html", context={})
        response.headers["Cache-Control"] = "no-store"
        return response

    @web_router.get("/dashboard/health")
    async def dashboard_health(
        dashboard: DashboardFacade = Depends(get_dashboard_services),
    ) -> Dict[str, Any]:
        return dashboard.dashboard_health()

    @controller_router.get("/api/controller/status")
    async def controller_status(
        dashboard: DashboardFacade = Depends(get_dashboard_services),
    ) -> Dict[str, Any]:
        return dashboard.controller_status()

    @controller_router.get("/api/bootstrap")
    async def bootstrap(
        dashboard: DashboardFacade = Depends(get_dashboard_services),
    ) -> Dict[str, Any]:
        return await dashboard.bootstrap()

    @events_router.get("/api/events")
    async def event_stream(
        request: Request, dashboard: DashboardFacade = Depends(get_dashboard_services)
    ) -> StreamingResponse:
        return StreamingResponse(
            dashboard_event_stream(dashboard, supplied_dashboard_token(request)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @settings_router.get("/api/settings")
    async def get_settings(
        dashboard: DashboardFacade = Depends(get_dashboard_services),
    ) -> Dict[str, Any]:
        return dashboard.settings()

    @settings_router.put("/api/settings")
    async def update_settings(
        payload: ClusterSettingsPayload,
        request: Request,
        dashboard: DashboardFacade = Depends(get_dashboard_services),
    ) -> Dict[str, Any]:
        try:
            return dashboard.update_settings(
                payload,
                supplied_token=payload.dashboard_token or supplied_dashboard_token(request),
                token_is_valid=dashboard_token_is_valid,
            )
        except ValueError as exc:
            return _error_response(exc)

    @controller_router.get("/api/status")
    async def get_status(
        dashboard: DashboardFacade = Depends(get_dashboard_services),
    ) -> Dict[str, Any]:
        return dashboard.status()

    @models_router.get("/api/models")
    async def get_models(
        dashboard: DashboardFacade = Depends(get_dashboard_services),
    ) -> Dict[str, Any]:
        return await dashboard.models()

    @nodes_router.post("/api/status/refresh")
    async def refresh_status(
        dashboard: DashboardFacade = Depends(get_dashboard_services),
    ) -> Dict[str, Any]:
        return dashboard.refresh_status()

    @nodes_router.post("/api/network/scan")
    async def scan_network(
        force: bool = False, dashboard: DashboardFacade = Depends(get_dashboard_services)
    ) -> Dict[str, Any]:
        return await dashboard.scan_network(force)

    @nodes_router.post("/api/onboarding/ssh-key")
    async def create_controller_ssh_identity(
        dashboard: DashboardFacade = Depends(get_dashboard_services),
    ) -> Dict[str, Any]:
        try:
            return await asyncio.to_thread(dashboard.create_controller_ssh_identity)
        except ValueError as exc:
            return _error_response(exc)

    @nodes_router.post("/api/nodes/probe")
    async def probe_unregistered_node(
        payload: NodePayload, dashboard: DashboardFacade = Depends(get_dashboard_services)
    ) -> Dict[str, Any]:
        try:
            return await dashboard.probe_candidate(payload)
        except ValueError as exc:
            return _error_response(exc)

    @nodes_router.post("/api/nodes")
    async def upsert_node(
        payload: NodePayload, dashboard: DashboardFacade = Depends(get_dashboard_services)
    ) -> Dict[str, Any]:
        try:
            return dashboard.upsert_node(payload)
        except ValueError as exc:
            return _error_response(exc)

    @nodes_router.patch("/api/nodes/{node_name}/name")
    async def rename_node(
        node_name: str,
        payload: NodeRenamePayload,
        dashboard: DashboardFacade = Depends(get_dashboard_services),
    ) -> Dict[str, Any]:
        try:
            return dashboard.rename_node(node_name, payload.new_name)
        except ValueError as exc:
            return _error_response(exc)

    @nodes_router.delete("/api/nodes/{node_name}")
    async def delete_node(
        node_name: str,
        payload: NodeDeletePayload | None = None,
        dashboard: DashboardFacade = Depends(get_dashboard_services),
    ) -> Dict[str, Any]:
        try:
            request = payload or NodeDeletePayload()
            return await asyncio.to_thread(
                dashboard.delete_node,
                node_name,
                remove_worker_files=request.remove_worker_files,
                confirmed=request.confirmed,
            )
        except ValueError as exc:
            return _error_response(exc)

    @nodes_router.get("/api/nodes/{node_name}/power")
    async def get_jetson_power_modes(
        node_name: str, dashboard: DashboardFacade = Depends(get_dashboard_services)
    ) -> Dict[str, Any]:
        try:
            return await dashboard.jetson_power_status(node_name)
        except ValueError as exc:
            return _error_response(exc)

    @nodes_router.post("/api/nodes/{node_name}/power")
    async def set_jetson_power_mode(
        node_name: str,
        payload: JetsonPowerModePayload,
        dashboard: DashboardFacade = Depends(get_dashboard_services),
    ) -> Dict[str, Any]:
        try:
            return dashboard.start_jetson_power_mode(node_name, payload.mode_id)
        except ValueError as exc:
            return _error_response(exc)

    @actions_router.post("/api/actions")
    async def start_action(
        payload: ActionPayload, dashboard: DashboardFacade = Depends(get_dashboard_services)
    ) -> Dict[str, Any]:
        try:
            return dashboard.start_action(payload)
        except ValueError as exc:
            return _error_response(exc)

    @actions_router.get("/api/actions")
    async def list_actions(
        dashboard: DashboardFacade = Depends(get_dashboard_services),
    ) -> Dict[str, Any]:
        return dashboard.listed_actions()

    @environment_router.get("/api/environment")
    async def get_environment(
        dashboard: DashboardFacade = Depends(get_dashboard_services),
    ) -> Dict[str, Any]:
        return dashboard.environment()

    @experiments_router.post("/api/experiments")
    async def start_experiment(
        payload: ExperimentPayload, dashboard: DashboardFacade = Depends(get_dashboard_services)
    ) -> Dict[str, Any]:
        try:
            return dashboard.start_experiment(payload)
        except ValueError as exc:
            return _error_response(exc)

    @experiments_router.get("/api/experiments")
    async def list_experiments(
        dashboard: DashboardFacade = Depends(get_dashboard_services),
    ) -> Dict[str, Any]:
        return dashboard.experiments()

    @experiments_router.get("/api/experiment-groups")
    async def list_experiment_groups(
        dashboard: DashboardFacade = Depends(get_dashboard_services),
    ) -> Dict[str, Any]:
        return dashboard.experiment_groups()

    @experiments_router.post("/api/experiments/cancel")
    async def cancel_experiment(
        dashboard: DashboardFacade = Depends(get_dashboard_services),
    ) -> Dict[str, Any]:
        try:
            return dashboard.cancel_experiment()
        except ValueError as exc:
            return _error_response(exc)

    @results_router.get("/api/runs/{run_id}")
    async def get_run(
        run_id: str, dashboard: DashboardFacade = Depends(get_dashboard_services)
    ) -> Dict[str, Any]:
        try:
            return dashboard.run(run_id)
        except ValueError as exc:
            return _error_response(exc)

    @results_router.get("/api/runs/{run_id}/responses")
    async def get_run_responses(
        run_id: str, dashboard: DashboardFacade = Depends(get_dashboard_services)
    ) -> Dict[str, Any]:
        try:
            return dashboard.responses(run_id)
        except ValueError as exc:
            return _error_response(exc)

    @results_router.delete("/api/runs/{run_id}")
    async def delete_run(
        run_id: str, dashboard: DashboardFacade = Depends(get_dashboard_services)
    ) -> Dict[str, Any]:
        try:
            return dashboard.delete_run(run_id)
        except ValueError as exc:
            return _error_response(exc)

    app.include_router(web_router)
    app.include_router(controller_router)
    app.include_router(nodes_router)
    app.include_router(environment_router)
    app.include_router(actions_router)
    app.include_router(models_router)
    app.include_router(settings_router)
    app.include_router(events_router)
    app.include_router(experiments_router)
    app.include_router(results_router)


def dashboard_event_stream(dashboard: DashboardFacade, supplied_token: str):
    # Event streaming remains a service-owned subscription; this helper avoids
    # exposing the global EventBus to a route implementation.
    del dashboard
    from cluster.dashboard import services

    return services.events.stream(supplied_token)


def dashboard_token_is_valid(token: str) -> bool:
    from cluster.dashboard import services

    return services.dashboard_token_is_valid(token)

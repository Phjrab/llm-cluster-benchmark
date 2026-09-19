"""FastAPI-only dependency adapters for Dashboard routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

from cluster.dashboard import services

if TYPE_CHECKING:
    from cluster.dashboard.services import DashboardFacade


def supplied_dashboard_token(request: Request) -> str:
    # Credentials must never be accepted from a URL: query strings are commonly
    # retained by browser history, proxies, and access logs.
    return request.headers.get("X-Cluster-Token", "")


def verify_token(request: Request) -> None:
    if not services.read_settings()["dashboard_token_auth"]:
        return
    if not services.dashboard_token_is_valid(supplied_dashboard_token(request)):
        raise HTTPException(status_code=401, detail="Dashboard access token is missing or invalid")


def verify_sweep_body_size(request: Request) -> None:
    raw = request.headers.get("content-length")
    if not raw:
        return
    try:
        size = int(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc
    if size < 0 or size > 1_048_576:
        raise HTTPException(status_code=413, detail="Sweep request body exceeds 1 MiB")


def get_dashboard_services(request: Request) -> "DashboardFacade":
    return request.app.state.dashboard_services

"""Pure worker-only RPC coordinator selection policy."""

from __future__ import annotations

from typing import Any, List, Mapping, Optional, Sequence

from cluster.domain.errors import ClusterError, ErrorCode


class RpcBackendError(ClusterError):
    """Stable RPC selection/lifecycle failure ready for Phase 09 persistence."""


def _worker_nodes(nodes: Sequence[Any]) -> List[Any]:
    workers: List[Any] = []
    for node in nodes:
        role = getattr(node, "role", "worker")
        if not hasattr(node, "name") or role != "worker":
            raise RpcBackendError(
                "RPC coordinator and participants must be selected workers",
                code=ErrorCode.CONFIG_MISMATCH,
                stage="rpc_coordinator_selection",
                node=getattr(node, "name", None),
            )
        workers.append(node)
    return workers


def select_rpc_coordinator(
    nodes: Sequence[Any],
    explicit: Optional[str] = None,
    platform_by_name: Optional[Mapping[str, str]] = None,
) -> Any:
    """Choose only from selected workers: explicit, Jetson first, then Pi."""
    workers = _worker_nodes(nodes)
    if len(workers) < 2:
        raise RpcBackendError(
            "Model-parallel RPC requires at least two selected workers",
            code=ErrorCode.CONFIG_MISMATCH,
            stage="rpc_coordinator_selection",
        )
    by_name = {node.name: node for node in workers}
    if explicit:
        if explicit not in by_name:
            raise RpcBackendError(
                f"RPC coordinator is not a selected worker: {explicit}",
                code=ErrorCode.CONFIG_MISMATCH,
                stage="rpc_coordinator_selection",
                node=explicit,
            )
        return by_name[explicit]

    platforms = dict(platform_by_name or {})
    for preferred in ("jetson", "raspberry-pi"):
        for node in workers:
            platform = platforms.get(node.name, str(getattr(node, "platform", "auto")))
            if platform == preferred:
                return node
    return workers[0]


__all__ = ["RpcBackendError", "select_rpc_coordinator"]

"""Deterministic scheduling primitives for formal research campaigns.

The scheduler is deliberately pure.  It never reads clocks, files, Workers,
or benchmark results, so a frozen matrix, repeat count, and seed always produce
the same campaign order on every Controller.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence


class ScheduleValidationError(ValueError):
    """A campaign schedule cannot be produced without ambiguity."""


def _positive_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ScheduleValidationError(f"{label} must be a positive integer")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScheduleValidationError(f"{label} must be a non-empty string")
    return value.strip()


def _seeded_rank(seed: int, namespace: str, value: str) -> tuple[str, str]:
    material = f"campaign-order-v1\0{seed}\0{namespace}\0{value}".encode("utf-8")
    return hashlib.sha256(material).hexdigest(), value


def seeded_randomized_block_order(
    base_cells: Sequence[Mapping[str, Any]],
    *,
    repeat_count: int,
    seed: int,
) -> list[dict[str, Any]]:
    """Expand and order matrix cells by deterministic randomized blocks.

    Each repeat is independent.  Within a repeat, block order and cell order
    are ranked by SHA-256 of the declared seed and immutable cell identity.
    Hash ranking avoids dependence on Python's random implementation while
    retaining the protocol's seeded randomized-block semantics.
    """
    repeat_count = _positive_integer(repeat_count, "repeat_count")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ScheduleValidationError("seed must be an integer")
    if not base_cells:
        raise ScheduleValidationError("base_cells must not be empty")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(base_cells):
        cell = dict(raw)
        cell_id = _text(cell.get("cell_id"), f"base_cells[{index}].cell_id")
        _text(cell.get("platform_cohort"), f"{cell_id}.platform_cohort")
        _text(cell.get("order_block"), f"{cell_id}.order_block")
        if cell_id in seen:
            raise ScheduleValidationError(f"duplicate cell_id: {cell_id}")
        seen.add(cell_id)
        normalized.append(cell)

    ordered: list[dict[str, Any]] = []
    order_index = 0
    for repeat_index in range(1, repeat_count + 1):
        blocks: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for cell in normalized:
            key = (str(cell["platform_cohort"]), str(cell["order_block"]))
            blocks[key].append(cell)
        block_keys = sorted(
            blocks,
            key=lambda key: _seeded_rank(
                seed,
                f"repeat:{repeat_index}:block",
                "\0".join(key),
            ),
        )
        for block_key in block_keys:
            block_namespace = "\0".join(block_key)
            block_cells = sorted(
                blocks[block_key],
                key=lambda cell: _seeded_rank(
                    seed,
                    f"repeat:{repeat_index}:cell:{block_namespace}",
                    str(cell["cell_id"]),
                ),
            )
            for cell in block_cells:
                order_index += 1
                campaign_cell = dict(cell)
                campaign_cell.update(
                    {
                        "campaign_cell_id": (
                            f"{cell['cell_id']}--repeat-{repeat_index:02d}"
                        ),
                        "repeat_index": repeat_index,
                        "order_index": order_index,
                    }
                )
                ordered.append(campaign_cell)
    return ordered


def coverage_from_cells(cells: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Return exhaustive status counts without silently dropping unknowns."""
    counts = {
        "planned": 0,
        "pending": 0,
        "running": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
        "excluded": 0,
    }
    for cell in cells:
        counts["planned"] += 1
        status = str(cell.get("status") or "pending")
        if status not in counts or status == "planned":
            raise ScheduleValidationError(f"unknown campaign cell status: {status}")
        counts[status] += 1
    return counts


__all__ = [
    "ScheduleValidationError",
    "coverage_from_cells",
    "seeded_randomized_block_order",
]

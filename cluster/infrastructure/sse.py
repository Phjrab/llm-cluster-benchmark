"""Pure Server-Sent Event parsing for WorkerClient adapters."""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Iterator


def parse_sse_events(lines: Iterable[bytes | str]) -> Iterator[Dict[str, Any]]:
    """Yield JSON data events; ignore blanks/comments and flag malformed data."""
    for raw in lines:
        line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
        line = line.strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload:
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            yield {"type": "malformed", "raw": payload}
            continue
        if isinstance(event, dict):
            yield event
        else:
            yield {"type": "malformed", "raw": payload}

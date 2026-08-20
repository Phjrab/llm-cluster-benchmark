"""HTTP worker API boundary with explicit token/header construction."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional

from .sse import parse_sse_events


@dataclass(frozen=True)
class WorkerResponse:
    ok: bool
    payload: Dict[str, Any]
    error: str = ""


class WorkerClient:
    def __init__(self, api_url: str, token: str = "") -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token

    def headers(self, extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Cluster-Worker-Token"] = self.token
        if extra:
            headers.update(extra)
        return headers

    def request_json(self, path: str, payload: Mapping[str, Any], timeout: float = 30.0) -> WorkerResponse:
        request = urllib.request.Request(
            f"{self.api_url}/{path.lstrip('/')}", data=json.dumps(dict(payload)).encode("utf-8"),
            headers=self.headers(), method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                value = json.loads(response.read().decode("utf-8"))
            return WorkerResponse(True, value if isinstance(value, dict) else {})
        except (OSError, ValueError, urllib.error.URLError) as exc:
            return WorkerResponse(False, {}, str(exc))

    def parse_stream(self, lines: Iterable[bytes | str]) -> Iterable[Dict[str, Any]]:
        return parse_sse_events(lines)

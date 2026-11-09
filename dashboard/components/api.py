"""Shared HTTP client helpers for talking to the LinguaProctor FastAPI backend.

Every page goes through :func:`api_request`, which never raises and never
substitutes mock data: it returns ``(data, None)`` on success or
``(None, error_message)`` on failure so the caller can show the error.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import streamlit as st

API_BASE_URL: str = os.getenv("LINGUAPROCTOR_API_URL", "http://localhost:8000").rstrip("/")
API_PREFIX: str = "/api/v1"
REQUEST_TIMEOUT: float = float(os.getenv("LINGUAPROCTOR_TIMEOUT", "120"))

DEMO_MODE_KEY = "demo_mode"


def is_demo_mode() -> bool:
    """Return True when the user explicitly enabled the sidebar "Demo data" toggle."""
    return bool(st.session_state.get(DEMO_MODE_KEY, False))


def demo_banner() -> None:
    """Show an unmistakable banner that the page is rendering canned demo data."""
    st.warning(
        "DEMO DATA: the results below are static sample data, not output from the API. "
        "Turn off **Demo data** in the sidebar to use the live backend."
    )


def _format_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:500] or resp.reason_phrase
    detail = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(detail, list):  # FastAPI 422 validation errors
        parts = []
        for err in detail:
            if isinstance(err, dict):
                loc = ".".join(str(x) for x in err.get("loc", []) if x != "body")
                parts.append(f"{loc}: {err.get('msg', err)}" if loc else str(err.get("msg", err)))
            else:
                parts.append(str(err))
        return "; ".join(parts)
    return str(detail)


_STATUS_LABELS: dict[int, str] = {
    404: "Not found",
    409: "Conflict",
    422: "Invalid input",
    502: "Upstream AI service failed",
    503: "AI service not configured / unavailable",
}


def api_request(
    method: str, path: str, *, timeout: float | None = None, **kwargs: Any
) -> tuple[Any | None, str | None]:
    """Call ``{API_BASE_URL}/api/v1{path}``.

    ``timeout`` overrides ``REQUEST_TIMEOUT`` for slow endpoints (e.g. generation).

    Returns:
        ``(json_body, None)`` on 2xx, otherwise ``(None, human-readable error)``.
    """
    url = f"{API_BASE_URL}{API_PREFIX}{path}"
    effective_timeout = REQUEST_TIMEOUT if timeout is None else timeout
    try:
        with httpx.Client(timeout=effective_timeout) as client:
            resp = client.request(method, url, **kwargs)
    except httpx.ConnectError:
        return None, f"Cannot reach the API at {API_BASE_URL} (connection refused)."
    except httpx.TimeoutException:
        return None, f"Request to {url} timed out after {effective_timeout:.0f}s."
    except httpx.HTTPError as exc:
        return None, f"HTTP error calling {url}: {exc}"

    if resp.is_success:
        try:
            return resp.json(), None
        except ValueError:
            return None, f"API returned non-JSON response (HTTP {resp.status_code})."

    label = _STATUS_LABELS.get(resp.status_code, "API error")
    return None, f"{label} (HTTP {resp.status_code}): {_format_detail(resp)}"


def check_health() -> tuple[bool, str]:
    """Probe ``GET /health`` (outside the /api/v1 prefix)."""
    try:
        with httpx.Client(timeout=5) as client:
            resp = client.get(f"{API_BASE_URL}/health")
    except httpx.ConnectError:
        return False, "Connection refused"
    except httpx.TimeoutException:
        return False, "Timeout"
    except httpx.HTTPError as exc:
        return False, str(exc)
    return (True, "Connected") if resp.status_code == 200 else (False, f"HTTP {resp.status_code}")

from typing import Any, Dict

from session_state_store import append_journal_event
from utils import truncate_middle


TRACE_SCHEMA = "runtime_trace/v1"


def _sanitize_scalar(value: Any, max_len: int = 320):
    if value is None:
        return None
    if isinstance(value, (bool, int, float)):
        return value
    text = str(value).strip()
    if not text:
        return ""
    return truncate_middle(text, max(80, int(max_len or 320)))


def _sanitize_data(data: Dict[str, Any], max_items: int = 12):
    payload = {}
    for idx, (key, raw_value) in enumerate((data or {}).items()):
        if idx >= max(1, int(max_items or 12)):
            break
        name = str(key or "").strip()
        if not name:
            continue
        if isinstance(raw_value, dict):
            inner = {}
            for jdx, (ik, iv) in enumerate(raw_value.items()):
                if jdx >= 10:
                    break
                inner[str(ik)] = _sanitize_scalar(iv, max_len=240)
            payload[name] = inner
        elif isinstance(raw_value, (list, tuple, set)):
            payload[name] = [_sanitize_scalar(item, max_len=160) for item in list(raw_value)[:10]]
        else:
            payload[name] = _sanitize_scalar(raw_value, max_len=320)
    return payload


def append_runtime_trace(
    session_json_path: str,
    *,
    session_id: str,
    iteration: int,
    stage: str,
    severity: str = "info",
    message: str = "",
    data: Dict[str, Any] = None,
) -> str:
    level = str(severity or "info").strip().lower()
    if level not in {"info", "warn", "error"}:
        level = "info"
    payload = {
        "schema": TRACE_SCHEMA,
        "session_id": str(session_id or ""),
        "iteration": int(iteration or 0),
        "stage": str(stage or "event"),
        "severity": level,
        "message": _sanitize_scalar(message, max_len=520) or "",
        "data": _sanitize_data(data or {}),
    }
    return append_journal_event(session_json_path, "runtime_trace", payload)

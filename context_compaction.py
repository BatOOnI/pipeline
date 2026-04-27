import re
from typing import Dict, List, Sequence, Tuple

from utils import truncate_middle

_CRITICAL_MARKERS = (
    "ITER ",
    "VERIFY",
    "PERMISSION DENIED",
    "ACTION FORMAT VIOLATION",
    "SOURCE FILE POLICY VIOLATION",
    "TRANSFORM VERIFY",
    "NO REAL PROGRESS",
    "STOP",
    "RUN SUMMARY",
    "FAILURE SUMMARY",
    "ERROR",
    "FAIL",
    "FINAL ANSWER",
)

_LOW_VALUE_PREFIXES = (
    "TASK PROFILE:",
    "MODEL ROUTE:",
    "LOCAL MODEL:",
    "CREATE STRATEGY:",
    "CREATE PHASE:",
    "PATCH PHASE:",
    "PATCH STRATEGY:",
    "PATCH HOTSPOT SELECTED:",
    "PATCH HOTSPOT SCORE:",
    "GIT CHECKPOINT TARGET:",
    "GIT CHECKPOINT OK",
    "GIT CHECKPOINT CONTEXT:",
)


def _normalize(line: str) -> str:
    return str(line or "").strip()


def _is_critical(line: str) -> bool:
    text = _normalize(line).upper()
    return any(marker in text for marker in _CRITICAL_MARKERS)


def _low_value_key(line: str) -> str:
    text = _normalize(line)
    upper = text.upper()
    for prefix in _LOW_VALUE_PREFIXES:
        if upper.startswith(prefix):
            return prefix
    return ""


def _dedupe_contiguous(lines: Sequence[str]) -> List[str]:
    compacted: List[str] = []
    for raw in lines:
        line = _normalize(raw)
        if not line:
            continue
        if compacted and compacted[-1] == line:
            continue
        compacted.append(line)
    return compacted


def compact_runtime_history(
    history: Sequence[str],
    *,
    level: int,
    max_items: int,
    max_chars: int,
) -> Tuple[str, Dict[str, int]]:
    lines = _dedupe_contiguous(history or [])
    if not lines:
        return "", {"input_lines": 0, "kept_lines": 0, "dropped_lines": 0, "compacted": 0}

    keep_recent_from = max(0, len(lines) - max(1, int(max_items or 1)))

    low_value_latest: Dict[str, int] = {}
    for idx in range(len(lines) - 1, -1, -1):
        key = _low_value_key(lines[idx])
        if key and key not in low_value_latest:
            low_value_latest[key] = idx

    selected: List[str] = []
    for idx, line in enumerate(lines):
        is_recent = idx >= keep_recent_from
        is_critical = _is_critical(line)
        low_key = _low_value_key(line)
        keep_low_value = bool(low_key) and idx == low_value_latest.get(low_key)

        if is_recent or is_critical or keep_low_value:
            selected.append(line)

    if not selected:
        selected = lines[-max(1, int(max_items or 1)) :]

    joined = "\n".join(selected)
    output = truncate_middle(joined, max(120, int(max_chars or 120)))

    dropped = max(0, len(lines) - len(selected))
    compacted = 1 if dropped > 0 else 0
    if dropped > 0:
        marker = f"[context compacted: kept {len(selected)}/{len(lines)} lines | level={int(level or 0)}]"
        marker_budget = max(120, int(max_chars or 120))
        output = truncate_middle((output + "\n" + marker).strip(), marker_budget)

    stats = {
        "input_lines": len(lines),
        "kept_lines": len(selected),
        "dropped_lines": dropped,
        "compacted": compacted,
    }
    return output, stats

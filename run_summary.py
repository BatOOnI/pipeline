import re
from typing import Iterable, List, Tuple

from utils import truncate_middle


_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([^\s\"']+)"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{10,}\b"),
    re.compile(r"\b(?:openai|lmstudio|deepseek)[_\-]?api[_\-]?key\s*[:=]\s*([^\s\"']+)", re.IGNORECASE),
]


def sanitize_summary_text(text: str, max_len: int = 240) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    safe = value
    for pattern in _SECRET_PATTERNS:
        if "authorization" in pattern.pattern.lower():
            safe = pattern.sub(r"\1[REDACTED]", safe)
        elif "api" in pattern.pattern.lower():
            safe = pattern.sub(lambda m: re.sub(r"([:=]\s*)([^\s\"']+)", r"\1[REDACTED]", m.group(0)), safe)
        else:
            safe = pattern.sub("[REDACTED]", safe)
    safe = re.sub(r"\s+", " ", safe).strip()
    return truncate_middle(safe, max(80, int(max_len or 240)))


def build_output_status(task_shape: str, touched_files: Iterable[str], outputs_ok: bool = True, missing_outputs=None) -> str:
    if str(task_shape or "").strip() == "transform_copy_task":
        if outputs_ok:
            return "outputs=ok"
        missing = [sanitize_summary_text(item, max_len=90) for item in list(missing_outputs or []) if str(item or "").strip()]
        if not missing:
            return "outputs_missing=(unknown)"
        preview = ",".join(missing[:5])
        if len(missing) > 5:
            preview += f",...(+{len(missing) - 5})"
        return f"outputs_missing={preview}"
    touched = [str(item or "").strip() for item in list(touched_files or []) if str(item or "").strip()]
    if touched:
        return f"outputs={len(touched)} touched"
    return "(none)"


def classify_terminal_state(finished_success: bool, stop_reason: str, blocked_actions: int = 0) -> str:
    if finished_success:
        return "success"
    reason = str(stop_reason or "").lower()
    if "permission" in reason and ("blocked" in reason or "denied" in reason):
        return "blocked"
    if int(blocked_actions or 0) > 0 and "blocked" in reason:
        return "blocked"
    if any(token in reason for token in ("fail", "error", "missing", "violation", "reject")):
        return "failed"
    return "stopped"


def derive_risk_flags(stop_reason: str, blocked_actions: int = 0) -> List[str]:
    reason = str(stop_reason or "").lower()
    flags: List[str] = []
    if "permission" in reason and ("blocked" in reason or "denied" in reason):
        flags.append("permission_blocked")
    if "verify" in reason and "fail" in reason:
        flags.append("verify_failure")
    if "transform" in reason and ("missing" in reason or "outputs_missing" in reason):
        flags.append("transform_outputs_missing")
    if "no progress" in reason:
        flags.append("no_progress")
    if "parse" in reason:
        flags.append("parse_instability")
    if int(blocked_actions or 0) > 0 and "permission_blocked" not in flags:
        flags.append("actions_blocked")
    return flags


def next_safe_action(status_detail: str, risk_flags: Iterable[str]) -> str:
    detail = str(status_detail or "").strip().lower()
    flags = set(str(item or "").strip().lower() for item in (risk_flags or []))
    if detail == "success":
        return "Proceed to next task."
    if "permission_blocked" in flags or detail == "blocked":
        return "Adjust permission mode/rules or approve required actions, then retry."
    if "verify_failure" in flags:
        return "Inspect latest verification errors and apply focused fix."
    if "transform_outputs_missing" in flags:
        return "Re-run transform with explicit output targets and source constraints."
    if "no_progress" in flags:
        return "Narrow objective and force a deterministic single-step action."
    return "Review failure summary and retry with a smaller scoped instruction."


def summarize_blocked_observations(history: Iterable[str], max_items: int = 4, max_len: int = 600) -> str:
    items = []
    seen = set()
    for line in list(history or [])[-20:]:
        lowered = str(line or "").lower()
        if not any(token in lowered for token in ("blocked", "violation", "missing", "failed", "rejected")):
            continue
        cleaned = sanitize_summary_text(str(line), max_len=220)
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(cleaned)
    if not items:
        return "(none)"
    joined = " | ".join(items[-max(1, int(max_items or 4)):])
    return truncate_middle(joined, max(120, int(max_len or 600)))


def build_safe_run_summary(
    *,
    finished_success: bool,
    stop_reason: str,
    executed_actions: int,
    blocked_actions: int,
    output_status: str,
) -> Tuple[str, dict]:
    safe_reason = sanitize_summary_text(stop_reason, max_len=260) or "n/a"
    status = "success" if finished_success else "stopped"
    status_detail = classify_terminal_state(finished_success, safe_reason, blocked_actions=blocked_actions)
    risk_flags = derive_risk_flags(safe_reason, blocked_actions=blocked_actions)
    next_action = next_safe_action(status_detail, risk_flags)

    summary_line = (
        f"RUN SUMMARY: executed={int(executed_actions or 0)} blocked={int(blocked_actions or 0)} "
        f"stop_reason={safe_reason} status={status} {str(output_status or '(none)')}"
    )
    payload = {
        "status": status,
        "status_detail": status_detail,
        "stop_reason": safe_reason,
        "executed": int(executed_actions or 0),
        "blocked": int(blocked_actions or 0),
        "output_status": str(output_status or "(none)"),
        "risk_flags": list(risk_flags),
        "next_safe_action": next_action,
    }
    return summary_line, payload

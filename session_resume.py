from typing import Dict, List, Tuple


def _normalize_prompt_history(snapshot: dict) -> List[str]:
    prompts: List[str] = []
    if not isinstance(snapshot, dict):
        return prompts
    raw = snapshot.get("prompt_history") or []
    if isinstance(raw, list):
        for item in raw:
            text = ""
            if isinstance(item, dict):
                text = str(item.get("prompt") or item.get("text") or "").strip()
            else:
                text = str(item or "").strip()
            if text:
                prompts.append(text)
    return prompts


def derive_base_and_latest_prompt(snapshot: dict) -> Tuple[str, str]:
    prompts = _normalize_prompt_history(snapshot)
    if prompts:
        return prompts[0], prompts[-1]

    base = str((snapshot or {}).get("prompt") or (snapshot or {}).get("goal") or "").strip()
    latest = base
    return base, latest


def build_resume_summary(snapshot: dict, *, session_path: str = "", journal_present: bool = False) -> Dict[str, object]:
    snapshot = dict(snapshot or {})
    base_prompt, latest_prompt = derive_base_and_latest_prompt(snapshot)
    prompt_count = len(_normalize_prompt_history(snapshot))

    has_state = bool(snapshot)
    project_root = str(snapshot.get("project_root") or "").strip()
    session_id = str(snapshot.get("session_id") or "").strip()
    iteration_count = int(snapshot.get("iteration_count") or 0)
    mode = str(snapshot.get("mode") or "").strip() or "(unknown)"
    task_shape = str(snapshot.get("task_shape") or "").strip() or "(unknown)"

    return {
        "has_state": has_state,
        "session_path": str(session_path or ""),
        "journal_present": bool(journal_present),
        "session_id": session_id,
        "project_root": project_root,
        "iteration_count": iteration_count,
        "mode": mode,
        "task_shape": task_shape,
        "base_prompt": base_prompt,
        "latest_prompt": latest_prompt,
        "prompt_count": prompt_count,
    }


def format_resume_status(summary: dict) -> str:
    summary = dict(summary or {})
    if not bool(summary.get("has_state")):
        return "Resume: no prior session state"

    session_id = str(summary.get("session_id") or "").strip()
    session_short = session_id[:8] if session_id else "(no id)"
    iteration_count = int(summary.get("iteration_count") or 0)
    mode = str(summary.get("mode") or "(unknown)")
    task_shape = str(summary.get("task_shape") or "(unknown)")
    project_root = str(summary.get("project_root") or "").strip()

    root_part = project_root if project_root else "(no project root)"
    return (
        f"Resume: session={session_short} | iter={iteration_count} | "
        f"mode={mode} | shape={task_shape} | root={root_part}"
    )

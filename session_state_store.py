import json
import os
import sys
import time
import uuid
from pathlib import Path

DEFAULT_MAX_JOURNAL_BYTES = 2_000_000
DEFAULT_MAX_ROTATIONS = 3
_WARNED_CONTEXTS = set()


def _warn_internal(context: str, exc: Exception, once: bool = True) -> None:
    key = str(context or "").strip() or "internal"
    if once and key in _WARNED_CONTEXTS:
        return
    if once:
        _WARNED_CONTEXTS.add(key)
    try:
        sys.stderr.write(f"[session warning] {key}: {exc}\n")
        sys.stderr.flush()
    except Exception:
        pass


def now_ms() -> int:
    return int(time.time() * 1000)


def journal_path_for(session_json_path: str) -> str:
    path = Path(session_json_path)
    if path.suffix.lower() == ".json":
        return str(path.with_suffix(".jsonl"))
    return str(path) + ".jsonl"


def _rotate_journal(path: str, max_bytes: int = DEFAULT_MAX_JOURNAL_BYTES, max_files: int = DEFAULT_MAX_ROTATIONS) -> None:
    if not os.path.exists(path):
        return
    try:
        size = os.path.getsize(path)
    except Exception as exc:
        _warn_internal("journal size probe failed", exc)
        return
    if size <= int(max_bytes):
        return

    for index in range(int(max_files), 0, -1):
        src = path if index == 1 else f"{path}.{index - 1}"
        dst = f"{path}.{index}"
        if not os.path.exists(src):
            continue
        try:
            if os.path.exists(dst):
                os.remove(dst)
        except Exception as exc:
            _warn_internal("journal rotate remove destination failed", exc)
        try:
            os.replace(src, dst)
        except Exception as exc:
            _warn_internal("journal rotate replace failed", exc)


def append_journal_event(
    session_json_path: str,
    event_type: str,
    payload: dict,
    max_bytes: int = DEFAULT_MAX_JOURNAL_BYTES,
    max_files: int = DEFAULT_MAX_ROTATIONS,
) -> str:
    path = journal_path_for(session_json_path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    _rotate_journal(path, max_bytes=max_bytes, max_files=max_files)

    event = {
        "type": str(event_type or "event"),
        "ts_ms": now_ms(),
    }
    if isinstance(payload, dict):
        event.update(payload)

    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    return path


def ensure_session_meta(
    session_json_path: str,
    session_id: str,
    project_root: str = "",
    prompt: str = "",
    task_fingerprint: str = "",
) -> str:
    path = journal_path_for(session_json_path)
    try:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path
    except Exception as exc:
        _warn_internal("session meta pre-check failed", exc)

    if not str(session_id or "").strip():
        session_id = uuid.uuid4().hex

    append_journal_event(
        session_json_path,
        "session_meta",
        {
            "session_id": str(session_id),
            "project_root": str(project_root or ""),
            "prompt": str(prompt or ""),
            "task_fingerprint": str(task_fingerprint or ""),
            "schema": "session_journal/v1",
        },
    )
    return path


def append_prompt_entry(session_json_path: str, session_id: str, prompt: str, iteration: int = 0) -> str:
    return append_journal_event(
        session_json_path,
        "prompt_entry",
        {
            "session_id": str(session_id or ""),
            "iteration": int(iteration or 0),
            "prompt": str(prompt or ""),
        },
    )


def load_latest_session_snapshot(session_json_path: str, max_files: int = DEFAULT_MAX_ROTATIONS) -> dict:
    path = journal_path_for(session_json_path)
    candidates = [f"{path}.{index}" for index in range(int(max_files), 0, -1)] + [path]

    latest_state = {}
    latest_meta = {}
    prompt_entries = []

    for candidate in candidates:
        if not os.path.exists(candidate):
            continue
        try:
            with open(candidate, "r", encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(record, dict):
                        continue
                    record_type = str(record.get("type") or "")
                    if record_type == "session_meta":
                        latest_meta = dict(record)
                    elif record_type == "state_snapshot":
                        state = record.get("state")
                        if isinstance(state, dict):
                            latest_state = dict(state)
                    elif record_type == "prompt_entry":
                        prompt_text = str(record.get("prompt") or "").strip()
                        if prompt_text:
                            prompt_entries.append(
                                {
                                    "ts_ms": int(record.get("ts_ms") or 0),
                                    "prompt": prompt_text,
                                }
                            )
        except Exception as exc:
            _warn_internal(f"session snapshot load failed: {candidate}", exc)
            continue

    if not latest_state and not latest_meta and not prompt_entries:
        return {}

    merged = dict(latest_state)
    if isinstance(latest_meta, dict):
        if latest_meta.get("session_id") and not merged.get("session_id"):
            merged["session_id"] = latest_meta.get("session_id")
        if latest_meta.get("project_root") and not merged.get("project_root"):
            merged["project_root"] = latest_meta.get("project_root")
        if latest_meta.get("task_fingerprint") and not merged.get("task_fingerprint"):
            merged["task_fingerprint"] = latest_meta.get("task_fingerprint")

    if prompt_entries and not merged.get("prompt_history"):
        merged["prompt_history"] = prompt_entries[-40:]

    return merged


def state_snapshot_from_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}

    keep_keys = [
        "session_id",
        "goal",
        "prompt",
        "mode",
        "mode_reason",
        "permission_mode",
        "project_root",
        "target_files",
        "active_patch_target",
        "iteration_count",
        "last_runtime_error",
        "last_useful_observation_summary",
        "last_tool_envelope",
        "stuck_iterations",
        "create_strategy",
        "create_phase",
        "patch_strategy",
        "patch_phase",
        "task_profile",
        "model_route",
        "route_reason",
        "task_shape",
        "task_shape_reason",
        "task_fingerprint",
        "local_fallback_step",
        "no_progress_streak",
        "transform_phase",
        "transform_analysis_complete",
        "transform_last_verify_ok",
        "transform_source_read_seen",
        "prompt_history",
        "prompt_history_hash",
    ]

    snapshot = {}
    for key in keep_keys:
        if key in payload:
            snapshot[key] = payload.get(key)
    return snapshot


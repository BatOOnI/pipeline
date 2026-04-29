import os
import re
from dataclasses import dataclass

from supervision_layer import evaluate_terminal_outcome


TRANSFORM_TASK_SHAPE = "transform_copy_task"
PATCH_DONE_KEYWORDS = ("done", "complete", "completed", "finished", "ready", "gotowe", "zakonczone")


def _normalize_prompt_text(user_prompt):
    lower = (user_prompt or "").lower()
    return re.sub(r"\s+", " ", lower).strip()


def _normalize_rel_path(rel_path):
    rel_path = str(rel_path or "").replace("/", os.sep).replace("\\", os.sep).strip()
    rel_path = rel_path.strip('"').strip("'").strip("`")
    rel_path = rel_path.lstrip(".\\/").rstrip(".,:;")
    return rel_path


def _plan_indicates_completion(plan):
    lower = _normalize_prompt_text(plan)
    return any(keyword in lower for keyword in PATCH_DONE_KEYWORDS)


def _existing_verified_python_outputs(*, project_root, touched_paths, last_written_files, created_files, state_touched_files):
    seen = set()
    found = []
    candidates = []
    candidates.extend(list(touched_paths or []))
    candidates.extend(list(last_written_files or []))
    candidates.extend(list(created_files or []))
    candidates.extend(list(state_touched_files or []))
    for rel in candidates:
        clean = _normalize_rel_path(rel)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        if not clean.lower().endswith(".py"):
            continue
        abs_path = os.path.abspath(os.path.join(project_root, clean))
        if os.path.exists(abs_path):
            found.append(clean)
    return found


@dataclass(frozen=True)
class TerminalDecision:
    stop: bool
    outcome: str
    reason: str


@dataclass(frozen=True)
class RepeatedActionDecision:
    blocked: bool
    reason: str


@dataclass(frozen=True)
class NoProgressDecision:
    stop: bool
    reason: str
    shift_chunk_to_write: bool
    refresh_patch_grounding: bool
    trigger_recovery: bool


def decide_completion(
    *,
    mode,
    task_profile,
    task_intent,
    task_shape,
    plan,
    had_run_cmd,
    touched_paths,
    verify_ok,
    meaningful_materialization,
    active_patch_target,
    target_files,
    project_root,
    state_touched_files,
    created_files,
    last_written_files,
    transform_outputs_ready=False,
    transform_missing=None,
    transform_verify_ok=False,
    no_progress_streak=0,
):
    plan_done = _plan_indicates_completion(plan)
    patch_target_touched = bool(mode == "patch" and active_patch_target and active_patch_target in (touched_paths or []))

    required_targets = []
    seen_targets = set()
    for rel in (target_files or []):
        clean = _normalize_rel_path(rel)
        if not clean or clean in seen_targets:
            continue
        seen_targets.add(clean)
        required_targets.append(clean)

    missing_targets = []
    for rel in required_targets:
        abs_path = os.path.abspath(os.path.join(project_root, rel))
        if not os.path.exists(abs_path):
            missing_targets.append(rel)
    create_targets_ready = (not required_targets) or (not missing_targets)
    no_real_progress = bool(touched_paths and not meaningful_materialization)
    verified_python_outputs = _existing_verified_python_outputs(
        project_root=project_root,
        touched_paths=touched_paths,
        last_written_files=last_written_files,
        created_files=created_files,
        state_touched_files=state_touched_files,
    )

    if task_intent in {"inspect_only", "run_command_only", "read_only", "answer_only"}:
        if plan_done:
            return TerminalDecision(True, "success", "MODEL INDICATED COMPLETION -> STOP")
        return TerminalDecision(False, "retry", "READ-ONLY ANALYSIS IN PROGRESS -> CONTINUE")

    if (
        mode == "create"
        and task_profile == "standard_create"
        and verify_ok
        and no_real_progress
        and len(verified_python_outputs) == 1
    ):
        return TerminalDecision(True, "success", "STANDARD CREATE VERIFIED NO-OP -> STOP")

    decision = evaluate_terminal_outcome(
        mode=mode,
        task_intent=task_intent,
        verified_ok=verify_ok,
        had_run_cmd=bool(had_run_cmd),
        plan_done=bool(plan_done),
        patch_target_touched=bool(patch_target_touched),
        transform_outputs_ready=bool(transform_outputs_ready),
        transform_verify_ok=bool(transform_verify_ok),
        touched_paths_count=len(touched_paths or []),
        meaningful_materialization=bool(meaningful_materialization),
        create_targets_required=bool(required_targets),
        create_targets_ready=bool(create_targets_ready),
        verified_python_outputs_count=len(verified_python_outputs),
        no_real_progress=bool(no_real_progress),
        no_progress_streak=int(no_progress_streak or 0),
    )
    if decision.outcome != "success":
        return TerminalDecision(False, decision.outcome, decision.reason)

    if str(task_shape or "") == TRANSFORM_TASK_SHAPE:
        missing = [str(item) for item in (transform_missing or []) if str(item).strip()]
        if not transform_outputs_ready:
            suffix = ", ".join(missing) if missing else "(unknown)"
            return TerminalDecision(False, "retry", f"TRANSFORM DONE BLOCKED: missing outputs -> {suffix}")
        if not transform_verify_ok:
            return TerminalDecision(False, "retry", "TRANSFORM DONE BLOCKED: verify has not passed for required outputs")
    return TerminalDecision(True, "success", decision.reason)


def decide_empty_done(
    *,
    mode,
    task_intent,
    task_shape,
    transform_outputs_ready,
    transform_missing,
    transform_verify_ok,
    runtime_error_present,
    permission_blocked,
    no_progress_streak,
):
    if str(task_shape or "") == TRANSFORM_TASK_SHAPE:
        missing = [str(item) for item in (transform_missing or []) if str(item).strip()]
        if not transform_outputs_ready:
            suffix = ", ".join(missing) if missing else "(unknown)"
            return TerminalDecision(False, "retry", f"EMPTY DONE BLOCKED: missing required transform outputs -> {suffix}")
        if not transform_verify_ok:
            return TerminalDecision(False, "retry", "EMPTY DONE BLOCKED: verification has not passed for transform outputs")

    decision = evaluate_terminal_outcome(
        mode=mode,
        task_intent=task_intent,
        verified_ok=True,
        had_run_cmd=False,
        plan_done=True,
        patch_target_touched=False,
        transform_outputs_ready=True,
        transform_verify_ok=bool(transform_verify_ok),
        touched_paths_count=0,
        meaningful_materialization=False,
        create_targets_required=False,
        create_targets_ready=True,
        empty_done=True,
        runtime_error_present=bool(runtime_error_present),
        permission_blocked=bool(permission_blocked),
        no_progress_streak=int(no_progress_streak or 0),
    )
    if decision.outcome == "retry":
        return TerminalDecision(False, "retry", decision.reason)
    return TerminalDecision(True, decision.outcome, decision.reason)


def decide_repeated_action(*, repeated_count, repeat_limit, action_type):
    if int(repeated_count or 0) >= int(repeat_limit or 0):
        return RepeatedActionDecision(True, f"BLOCK REPEATED ACTION: {action_type}")
    return RepeatedActionDecision(False, "")


def decide_no_progress(
    *,
    mode,
    task_intent,
    verify_ok,
    had_run_cmd,
    active_patch_target,
    touched_paths,
    meaningful_materialization,
    runtime_error_present,
    no_progress_streak,
    create_strategy,
):
    patch_target_touched = bool(active_patch_target and active_patch_target in (touched_paths or []))
    stall_decision = evaluate_terminal_outcome(
        mode=mode,
        task_intent=task_intent,
        verified_ok=bool(verify_ok),
        had_run_cmd=bool(had_run_cmd),
        plan_done=False,
        patch_target_touched=patch_target_touched,
        touched_paths_count=len(touched_paths or []),
        meaningful_materialization=bool(meaningful_materialization),
        create_targets_required=False,
        create_targets_ready=False,
        runtime_error_present=bool(runtime_error_present),
        no_progress_streak=int(no_progress_streak or 0),
    )
    stop = stall_decision.outcome == "failure"
    shift_chunk = bool(int(no_progress_streak or 0) >= 2 and mode == "create" and create_strategy == "chunked_rewrite")
    refresh_patch = bool(int(no_progress_streak or 0) >= 2 and mode == "patch")
    trigger_recovery = bool(int(no_progress_streak or 0) >= 4)
    reason = stall_decision.reason if stop else "repeated no real progress"
    return NoProgressDecision(
        stop=stop,
        reason=reason,
        shift_chunk_to_write=shift_chunk,
        refresh_patch_grounding=refresh_patch,
        trigger_recovery=trigger_recovery,
    )

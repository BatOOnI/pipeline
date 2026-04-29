import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


POLICY_DECISIONS = {
    "recover",
    "retry",
    "stop_success",
    "stop_blocked",
    "stop_partial",
    "ask_user",
    "escalate_provider",
}

SIDE_EFFECT_ACTIONS = {
    "run_cmd",
    "write_file",
    "replace_in_file",
    "insert_before",
    "insert_after",
    "replace_block",
    "patch_lines",
    "begin_file_rewrite",
    "append_file_chunk",
    "finalize_file_rewrite",
    "mkdir",
    "download_file",
    "http_get",
}

WINDOWS_BUILTINS = {"dir", "copy", "move", "del", "type", "echo", "ren", "erase"}


@dataclass
class SetupPolicyDecision:
    action: str
    reason: str = ""
    guidance: str = ""


@dataclass
class SetupPolicyState:
    shell_operator_retries: int = 0
    windows_builtin_retries: int = 0
    answer_mixed_retries: int = 0
    repeated_blocked_counts: Dict[str, int] = field(default_factory=dict)
    downloaded_files: List[str] = field(default_factory=list)
    created_files: List[str] = field(default_factory=list)
    successful_steps: List[str] = field(default_factory=list)
    failed_steps: List[str] = field(default_factory=list)
    optional_missing_files: List[str] = field(default_factory=list)
    real_blockers: List[str] = field(default_factory=list)
    inspected_files: List[str] = field(default_factory=list)
    folder_inspected_once: bool = False
    awaiting_post_inspect_resolution: bool = False


def is_setup_context(task_intent: str, create_strategy: str) -> bool:
    return str(task_intent or "").strip() == "run_command_only" or str(create_strategy or "").strip() == "run_cmd"


def enforce_setup_action_budget(actions: List[dict], max_side_effect_actions: int) -> Tuple[List[dict], str]:
    budget = max(1, int(max_side_effect_actions or 1))
    kept: List[dict] = []
    side_effect_count = 0
    total_side_effect = 0
    for action in actions:
        action_type = str(action.get("type", "")).strip()
        if action_type in SIDE_EFFECT_ACTIONS:
            total_side_effect += 1
    if total_side_effect <= budget:
        return list(actions), ""
    for action in actions:
        action_type = str(action.get("type", "")).strip()
        if action_type in SIDE_EFFECT_ACTIONS:
            if side_effect_count >= budget:
                continue
            side_effect_count += 1
        kept.append(action)
    note = (
        f"SETUP ACTION BUDGET: truncated side-effect actions {total_side_effect} -> {budget}. "
        "Execute in smaller batches."
    )
    return kept, note


def _normalize_block_key(action: dict, summary: str, details: str) -> str:
    action_type = str(action.get("type", "")).strip()
    args = action.get("args", {}) if isinstance(action.get("args"), dict) else {}
    cmd = str(args.get("cmd", "")).strip().lower()
    marker = "blocked"
    combined = f"{summary}\n{details}".lower()
    if "shell operators are not allowed" in combined:
        marker = "shell_operator_blocked"
    elif "path escapes project root" in combined:
        marker = "path_escape_blocked"
    elif "permission denied" in combined:
        marker = "permission_blocked"
    return json.dumps({"type": action_type, "cmd": cmd, "marker": marker}, ensure_ascii=False, sort_keys=True)


def _is_windows_builtin_failed(action: dict, summary: str, details: str) -> bool:
    action_type = str(action.get("type", "")).strip()
    if action_type != "run_cmd":
        return False
    if "CMD FAILED" not in str(summary or ""):
        return False
    if "WinError 2" not in str(details or "") and "not found" not in str(details or "").lower():
        return False
    args = action.get("args", {}) if isinstance(action.get("args"), dict) else {}
    cmd = str(args.get("cmd", "")).strip()
    if not cmd:
        return False
    parts = re.split(r"\s+", cmd)
    first = parts[0].strip().lower() if parts else ""
    return first in WINDOWS_BUILTINS


def _append_unique(items: List[str], value: str):
    clean = str(value or "").strip()
    if clean and clean not in items:
        items.append(clean)


def record_setup_observation(policy_state: SetupPolicyState, action: dict, observation) -> None:
    action_type = str(action.get("type", "")).strip()
    summary = str(getattr(observation, "summary", "") or "")
    details = str(getattr(observation, "details", "") or "")
    ok = bool(getattr(observation, "ok", False))
    path = str(getattr(observation, "path", "") or "")
    args = action.get("args", {}) if isinstance(action.get("args"), dict) else {}
    arg_path = str(args.get("path", args.get("output_path", "")) or "")
    file_name = os.path.basename((path or arg_path).replace("\\", "/")).strip().lower()
    if ok:
        _append_unique(policy_state.successful_steps, summary or action_type)
        if action_type in {"read_file", "find_in_file"} and file_name:
            _append_unique(policy_state.inspected_files, file_name)
        if action_type in {"download_file", "http_get"} and path:
            _append_unique(policy_state.downloaded_files, path)
        if action_type in {"write_file", "mkdir"} and path:
            _append_unique(policy_state.created_files, path)
        if policy_state.awaiting_post_inspect_resolution and action_type == "run_cmd":
            policy_state.awaiting_post_inspect_resolution = False
        return
    _append_unique(policy_state.failed_steps, details or summary or action_type)


def evaluate_setup_observation(
    policy_state: SetupPolicyState,
    action: dict,
    observation,
    blocked_repeat_limit: int,
) -> SetupPolicyDecision:
    summary = str(getattr(observation, "summary", "") or "")
    details = str(getattr(observation, "details", "") or "")
    ok = bool(getattr(observation, "ok", False))
    action_type = str(action.get("type", "")).strip()
    if ok:
        return SetupPolicyDecision("retry")

    combined = f"{summary}\n{details}".lower()
    args = action.get("args", {}) if isinstance(action.get("args"), dict) else {}
    arg_path = str(args.get("path", args.get("output_path", "")) or "")
    file_name = os.path.basename(arg_path.replace("\\", "/")).strip().lower()
    missing_file = any(token in combined for token in ("no such file", "not found", "404"))
    if (
        action_type in {"read_file", "find_in_file"}
        and file_name == "requirements.txt"
        and missing_file
        and (
            "pyproject.toml" in set(policy_state.inspected_files)
            or "setup.cfg" in set(policy_state.inspected_files)
        )
    ):
        _append_unique(policy_state.optional_missing_files, "requirements.txt")
        return SetupPolicyDecision("retry", "SETUP OPTIONAL MISSING: requirements.txt")

    if action_type == "action_format_violation" and "answer action contract violation" in combined:
        if policy_state.answer_mixed_retries < 1:
            policy_state.answer_mixed_retries += 1
            return SetupPolicyDecision(
                "recover",
                "SETUP RECOVERY: answer_mixed_with_tools",
                "Return tool actions only in this turn. Return final answer in a later turn.",
            )
        return SetupPolicyDecision(
            "stop_blocked",
            "SETUP BLOCKED: repeated answer+tools contract violation",
        )

    if action_type == "run_cmd" and "shell operators are not allowed in run_cmd" in combined:
        if policy_state.shell_operator_retries < 1:
            policy_state.shell_operator_retries += 1
            return SetupPolicyDecision(
                "recover",
                "SETUP RECOVERY: shell_operator_blocked",
                "Use run_cmd args.cwd and plain command (no cd && chain).",
            )
        return SetupPolicyDecision(
            "stop_blocked",
            "SETUP BLOCKED: repeated shell operator command block",
        )

    if _is_windows_builtin_failed(action, summary, details):
        if policy_state.windows_builtin_retries < 1:
            policy_state.windows_builtin_retries += 1
            return SetupPolicyDecision(
                "recover",
                "SETUP RECOVERY: windows_builtin_failed",
                "Use cmd /c <builtin> for Windows built-in commands.",
            )

    block_key = _normalize_block_key(action, summary, details)
    if (
        policy_state.awaiting_post_inspect_resolution
        and policy_state.downloaded_files
        and action_type == "run_cmd"
    ):
        return SetupPolicyDecision("retry")
    if action_type == "run_cmd":
        policy_state.repeated_blocked_counts[block_key] = int(policy_state.repeated_blocked_counts.get(block_key, 0) or 0) + 1
        if int(policy_state.repeated_blocked_counts[block_key]) >= max(2, int(blocked_repeat_limit or 2)):
            return SetupPolicyDecision(
                "stop_blocked",
                "SETUP BLOCKED: repeated same command blocked",
            )

    if str(getattr(observation, "tool", "")).strip() == "permission_denied":
        _append_unique(policy_state.real_blockers, "permission denied")
        return SetupPolicyDecision("ask_user", "SETUP BLOCKED: permission denied")

    if details or summary:
        _append_unique(policy_state.real_blockers, details or summary)
    return SetupPolicyDecision("retry")


def build_setup_partial_lines(policy_state: SetupPolicyState, external_created_files=None) -> List[str]:
    created = list(policy_state.created_files or [])
    for item in list(external_created_files or []):
        clean = str(item or "").strip()
        if clean and clean not in created:
            created.append(clean)
    downloaded = list(policy_state.downloaded_files or [])
    success = list(policy_state.successful_steps or [])
    failed = list(policy_state.failed_steps or [])
    optional_missing = list(policy_state.optional_missing_files or [])
    blockers = list(policy_state.real_blockers or [])

    lines = ["SETUP PARTIAL SUMMARY:"]
    lines.append("- completed steps: " + (", ".join(success[-5:]) if success else "(none)"))
    lines.append("- optional missing files: " + (", ".join(optional_missing[-4:]) if optional_missing else "(none)"))
    lines.append("- real blockers: " + (", ".join(blockers[-4:]) if blockers else (", ".join(failed[-4:]) if failed else "(none)")))
    files = created + [item for item in downloaded if item not in created]
    lines.append("- files created/downloaded: " + (", ".join(files[-6:]) if files else "(none)"))
    lines.append("- next manual step: inspect setup folder and run final configure command manually.")
    return lines

import re
import unicodedata
from dataclasses import dataclass


TASK_INTENTS = {
    "create_new_file",
    "edit_existing_file",
    "transform_source_to_output",
    "inspect_only",
    "run_command_only",
}

TERMINAL_OUTCOMES = {"success", "blocked", "retry", "failure"}


def _normalize(text):
    lowered = (text or "").lower()
    lowered = unicodedata.normalize("NFKD", lowered)
    return "".join(ch for ch in lowered if not unicodedata.combining(ch))


def _words(text):
    return set(re.findall(r"[a-z0-9_]+", _normalize(text)))


@dataclass
class TaskIntentDecision:
    intent: str
    reason: str


@dataclass
class TerminalOutcomeDecision:
    outcome: str
    reason: str


def infer_task_intent(user_prompt, existing_tokens=None, all_tokens=None, selected_files=""):
    text = _normalize(user_prompt)
    words = _words(user_prompt)
    existing_tokens = list(existing_tokens or [])
    all_tokens = list(all_tokens or [])
    selected_files = str(selected_files or "").strip()

    edit_words = {
        "patch",
        "fix",
        "replace",
        "repair",
        "update",
        "modify",
        "change",
        "improve",
        "improvement",
        "refactor",
        "debug",
        "edytuj",
        "napraw",
        "zmien",
        "popraw",
        "ulepsz",
        "ulepszenie",
    }
    create_words = {"create", "build", "make", "stworz", "utworz", "zrob", "napisz"}
    inspect_words = {"read", "inspect", "analyze", "analysis", "sprawdz", "przeczytaj", "review"}
    verify_markers = ("verify", "syntax", "compile", "py_compile", "check", "uruchom", "sprawdz")
    transform_markers = ("transform", "template", "szablon", "copy", "kopia", "based on", "from ")
    transform_output_markers = ("summary", "report", "analysis", "derived output", "podsumowanie", "raport")

    has_edit_words = bool(words.intersection(edit_words))
    has_create_words = bool(words.intersection(create_words))
    has_inspect_words = bool(words.intersection(inspect_words))
    has_verify_markers = any(marker in text for marker in verify_markers)
    has_transform_markers = any(marker in text for marker in transform_markers)
    has_existing_targets = bool(existing_tokens)
    has_many_targets = len(all_tokens) >= 2
    no_change_markers = (
        "do not modify",
        "don't modify",
        "do not change",
        "don't change",
        "without changes",
        "no changes",
        "no edits",
        "no edit",
        "read only",
        "read-only",
        "without writing",
        "no write",
        "nie modyfikuj",
        "nie zmieniaj",
        "bez zmian",
        "tylko odczyt",
    )
    has_no_change_guard = any(marker in text for marker in no_change_markers)
    command_markers = (
        "run command",
        "run_cmd",
        "execute",
        "git ",
        "python -m",
        "pytest",
        "py_compile",
        "shell",
        "cmd",
    )
    command_only_hints = ("just", "only", "report output", "and stop", "without changes", "do not modify")
    has_command_markers = any(marker in text for marker in command_markers)
    has_command_only_hint = any(marker in text for marker in command_only_hints)

    if has_inspect_words and has_no_change_guard and not has_create_words and not has_transform_markers:
        return TaskIntentDecision("inspect_only", "inspection-only language with no-change constraint")

    if selected_files:
        return TaskIntentDecision("edit_existing_file", "PATCH_FILES selected")

    if has_transform_markers and (has_many_targets or "output" in text or "new file" in text):
        return TaskIntentDecision("transform_source_to_output", "transform/copy intent with source/output context")

    if has_many_targets and has_create_words and any(marker in text for marker in transform_output_markers):
        return TaskIntentDecision("transform_source_to_output", "source/output summary-style intent")

    if has_command_markers and has_command_only_hint and not has_create_words and not has_transform_markers:
        return TaskIntentDecision("run_command_only", "command-only execution intent")

    if has_existing_targets and (has_edit_words or has_verify_markers) and not has_no_change_guard:
        return TaskIntentDecision("edit_existing_file", "existing-file improvement/edit intent")

    if has_inspect_words and not has_edit_words and not has_create_words and not has_verify_markers:
        return TaskIntentDecision("inspect_only", "inspection-only language")

    if has_create_words and not has_existing_targets:
        return TaskIntentDecision("create_new_file", "explicit create/build language")

    if has_existing_targets and has_no_change_guard and has_inspect_words:
        return TaskIntentDecision("inspect_only", "existing target with explicit read-only constraint")

    if has_existing_targets:
        return TaskIntentDecision("edit_existing_file", "existing file target inferred")

    return TaskIntentDecision("create_new_file", "default create intent")


def evaluate_terminal_outcome(
    *,
    mode,
    task_intent,
    verified_ok,
    had_run_cmd,
    plan_done,
    patch_target_touched,
    transform_outputs_ready=False,
    transform_verify_ok=False,
    touched_paths_count=0,
    meaningful_materialization=False,
    create_targets_required=False,
    create_targets_ready=False,
    verified_python_outputs_count=0,
    no_real_progress=False,
    empty_done=False,
    runtime_error_present=False,
    permission_blocked=False,
    no_progress_streak=0,
):
    mode = str(mode or "").strip().lower()
    task_intent = str(task_intent or "").strip()
    if task_intent not in TASK_INTENTS:
        task_intent = "create_new_file"

    if empty_done:
        if permission_blocked:
            return TerminalOutcomeDecision("blocked", "permission policy blocked execution")
        if runtime_error_present:
            return TerminalOutcomeDecision("retry", "EMPTY DONE BLOCKED: runtime or verify error still exists")
        return TerminalOutcomeDecision("success", "MODEL INDICATED COMPLETION -> STOP")

    if mode == "create":
        if (
            task_intent == "edit_existing_file"
            and int(no_progress_streak or 0) >= 2
            and touched_paths_count == 0
            and not meaningful_materialization
            and not runtime_error_present
        ):
            return TerminalOutcomeDecision("failure", "EDIT TASK STALLED: repeated read-only/no-op loop")

        if had_run_cmd and verified_ok:
            return TerminalOutcomeDecision("success", "EXECUTION/CHECK PASSED -> STOP")

        if task_intent == "transform_source_to_output":
            if transform_outputs_ready and transform_verify_ok and verified_ok:
                if touched_paths_count or meaningful_materialization:
                    return TerminalOutcomeDecision("success", "TRANSFORM OUTPUTS VERIFIED -> STOP")
                return TerminalOutcomeDecision("success", "TRANSFORM VERIFIED NO-OP -> STOP")
            return TerminalOutcomeDecision("retry", "WRITE/VERIFY OK BUT CONTINUE")

        if create_targets_required and create_targets_ready and verified_ok:
            return TerminalOutcomeDecision("success", "CREATE OUTPUTS VERIFIED -> STOP")

        if (
            task_intent == "edit_existing_file"
            and verified_ok
            and verified_python_outputs_count > 0
            and no_real_progress
        ):
            return TerminalOutcomeDecision("success", "EDIT VERIFIED NO-OP -> STOP")

        if verified_ok and (touched_paths_count > 0 or meaningful_materialization):
            if create_targets_required:
                return TerminalOutcomeDecision("retry", "WRITE/VERIFY OK BUT CONTINUE")
            return TerminalOutcomeDecision("success", "CREATE OUTPUTS VERIFIED -> STOP")

        if plan_done and verified_ok and (touched_paths_count > 0 or meaningful_materialization or had_run_cmd):
            return TerminalOutcomeDecision("success", "MODEL INDICATED COMPLETION -> STOP")

        return TerminalOutcomeDecision("retry", "WRITE/VERIFY OK BUT CONTINUE")

    if (
        task_intent == "edit_existing_file"
        and int(no_progress_streak or 0) >= 2
        and touched_paths_count == 0
        and not meaningful_materialization
        and not runtime_error_present
    ):
        return TerminalOutcomeDecision("failure", "EDIT TASK STALLED: repeated read-only/no-op loop")

    if patch_target_touched and verified_ok:
        return TerminalOutcomeDecision("success", "SINGLE-FILE PATCH VERIFIED -> STOP")
    if plan_done and patch_target_touched and verified_ok:
        return TerminalOutcomeDecision("success", "PATCH COMPLETE -> STOP")
    if had_run_cmd and patch_target_touched and verified_ok:
        return TerminalOutcomeDecision("success", "PATCH VERIFIED BY RUN -> STOP")
    return TerminalOutcomeDecision("retry", "PATCH APPLIED BUT CONTINUE")

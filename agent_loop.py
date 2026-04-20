import base64
import hashlib
import json
import os
import re
import shutil
import sys
import unicodedata
import uuid
import ast
from dataclasses import dataclass, field

import config
from executor import (
    append_file_chunk,
    begin_file_rewrite,
    finalize_file_rewrite,
    find_in_file,
    insert_after,
    insert_before,
    mkdir,
    patch_lines,
    read_file,
    read_file_snippet,
    replace_in_file,
    replace_block,
    run_cmd,
    verify_touched_paths,
    write_file,
)
from git_tools import git_checkpoint, git_init, git_is_repo
from parser import parse_response
from providers import call_model
from utils import coerce_int, ensure_gitignore, is_subpath, read_json_file, truncate_middle, write_json_file


PATCH_KEYWORDS = (
    "patch",
    "fix",
    "replace",
    "repair",
    "update",
    "modify",
    "change",
    "improve",
    "refactor",
    "debug",
    "edytuj",
    "napraw",
    "przerob",
    "zmien",
    "popraw",
    "usun",
)
CREATE_KEYWORDS = (
    "zrob",
    "napisz",
    "stworz",
    "utworz",
    "dodaj",
    "create",
    "build",
    "make",
)
EXISTING_EDIT_HINTS = (
    "moja gra",
    "mojej gry",
    "my game",
    "this game",
    "this app",
    "moja aplikacja",
    "existing file",
    "existing project",
    "w mojej grze",
)
PATCH_DONE_KEYWORDS = ("done", "complete", "completed", "finished", "ready", "gotowe", "zakonczone")
SESSION_DIR_RE = re.compile(r"^TEST-(\d+)$", re.IGNORECASE)
FILE_TOKEN_RE = re.compile(r"([A-Za-z0-9_./\\-]+\.[A-Za-z0-9_+-]+)")
CHUNK_STATUS_RE = re.compile(r"part=(\d+)/(\d+).*received=(\d+)", re.IGNORECASE)
BLOCKED_TARGET_TOKENS = {
    "path",
    "paths",
    "line",
    "traceback",
    "list.remove",
    "remove",
    "python",
    "json",
    "error",
    "stderr",
    "stdout",
}
PROGRESS_TOOLS = {
    "write_file",
    "replace_in_file",
    "insert_before",
    "insert_after",
    "replace_block",
    "patch_lines",
    "finalize_file_rewrite",
}
REWRITE_STATE_DIR = os.path.join(".agent", "rewrite_state")
PATCH_FILE_ACTIONS = {
    "write_file",
    "replace_in_file",
    "insert_before",
    "insert_after",
    "replace_block",
    "patch_lines",
    "begin_file_rewrite",
    "append_file_chunk",
    "finalize_file_rewrite",
    "find_in_file",
    "read_file",
}
ATOMIC_PATCH_FILE_ACTIONS = {
    "write_file",
    "replace_in_file",
    "insert_before",
    "insert_after",
    "replace_block",
    "patch_lines",
    "begin_file_rewrite",
    "append_file_chunk",
    "finalize_file_rewrite",
}
PATCH_TASK_SHAPES = {"single_file_patch", "multi_file_patch"}
TRANSFORM_TASK_SHAPES = {"transform_copy_task", "analysis_report_task"}
NON_PATCH_TASK_SHAPES = TRANSFORM_TASK_SHAPES.union({"generate_new_files_task", "project_generation_task"})
LARGE_FILE_ENABLED_THRESHOLD = 20000
LARGE_FILE_CHUNK_THRESHOLD = 60000
LARGE_FILE_STRICT_THRESHOLD = 120000
TEXT_TRANSFORM_HINTS = (
    "transform",
    "template",
    "placeholder",
    "analysis",
    "report",
    "summary",
    "map",
    "metadata",
    "scan",
    "analyze",
    "przeksztalc",
    "szablon",
    "copy",
    "kopia",
)


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)
            stream.flush()

    def flush(self):
        for stream in self.streams:
            stream.flush()


@dataclass
class PipelineState:
    goal: str
    mode: str
    active_project_root: str
    current_provider: str
    base_provider: str = ""
    mode_reason: str = ""
    target_files: list = field(default_factory=list)
    active_patch_target: str = ""
    expected_file_count: int = 1
    single_file_task: bool = False
    last_runtime_error: str = ""
    last_plan_fingerprint: str = ""
    stuck_iterations: int = 0
    duplicate_action_cache: dict = field(default_factory=dict)
    last_written_files: list = field(default_factory=list)
    touched_files: list = field(default_factory=list)
    created_files: list = field(default_factory=list)
    prompt_compaction_level: int = 0
    rescue_handoff_count: int = 0
    progress_happened: bool = False
    iteration_count: int = 0
    last_useful_observation_summary: str = ""
    prompt_changed: bool = False
    required_b64_fields: list = field(default_factory=list)
    create_strategy: str = "write_file"
    create_phase: str = "initial_create"
    create_strategy_reason: str = ""
    create_full_write_streak: int = 0
    last_created_main_file: str = ""
    chunk_session_open: bool = False
    chunk_target_path: str = ""
    chunk_expected_parts: int = 0
    chunk_received_parts: int = 0
    chunk_missing_parts: list = field(default_factory=list)
    chunk_finalize_pending: bool = False
    chunk_protocol_violation_streak: int = 0
    patch_phase: str = "inspect_target"
    patch_strategy: str = "surgical_patch"
    patch_strategy_reason: str = ""
    patch_exact_snippet: str = ""
    patch_hotspot_label: str = ""
    patch_task_intent: str = ""
    patch_hotspot_candidates: list = field(default_factory=list)
    patch_hotspot_primary_index: int = 0
    patch_hotspot_secondary_index: int = 1
    patch_hotspot_fail_counts: dict = field(default_factory=dict)
    patch_hotspot_last_fail_reason: str = ""
    patch_stale_error_detected: bool = False
    patch_stale_error_reason: str = ""
    patch_replace_miss_streak: int = 0
    patch_failure_streak: int = 0
    patch_verify_failure_streak: int = 0
    patch_syntax_failure_streak: int = 0
    patch_anchor_failure_streak: int = 0
    patch_broad_patch_streak: int = 0
    patch_hotspot_unavailable_streak: int = 0
    task_profile: str = ""
    model_route: str = ""
    route_reason: str = ""
    task_shape: str = "project_generation_task"
    task_shape_reason: str = ""
    task_fingerprint: str = ""
    source_readonly_files: list = field(default_factory=list)
    derived_allowed_files: list = field(default_factory=list)
    transform_primary_source: str = ""
    large_file_mode: str = "disabled"
    local_fallback_step: int = 0
    no_progress_streak: int = 0
    large_read_cache: dict = field(default_factory=dict)
    transform_last_verify_ok: bool = False
    transform_source_hash: str = ""
    transform_source_read_seen: bool = False
    transform_no_material_progress_streak: int = 0
    transform_phase: str = "transform_analyze"
    transform_analysis_complete: bool = False


def normalize_rel_path(rel_path: str) -> str:
    rel_path = (rel_path or "").replace("/", os.sep).replace("\\", os.sep).strip()
    rel_path = rel_path.strip('"').strip("'").strip("`")
    rel_path = rel_path.lstrip(".\\/").rstrip(".,:;")
    if not rel_path:
        return "app.py"
    return rel_path


def normalize_cmd_paths(cmd):
    def fix_one(part):
        if not isinstance(part, str):
            return part
        return part.replace("/", os.sep).replace("\\", os.sep).strip()

    if isinstance(cmd, list):
        return [fix_one(x) for x in cmd]
    return fix_one(cmd)


def _configured_root_path():
    raw = (config.PROJECT_ROOT or "TEST").strip()
    if not raw:
        raw = "TEST"
    if os.path.isabs(raw):
        return os.path.abspath(raw)
    return os.path.abspath(os.path.join(os.getcwd(), raw))


def _explicit_configured_root_path():
    raw = str(config.PROJECT_ROOT or "").strip()
    if not raw:
        return ""
    if os.path.isabs(raw):
        return os.path.abspath(raw)
    return os.path.abspath(os.path.join(os.getcwd(), raw))


def _session_container_info():
    configured = _configured_root_path()
    base_name = os.path.basename(configured)
    if SESSION_DIR_RE.match(base_name):
        return {
            "configured": configured,
            "container": os.path.dirname(configured) or os.getcwd(),
            "explicit_session": configured,
        }
    if base_name.upper() == "TEST":
        return {
            "configured": configured,
            "container": os.path.dirname(configured) or os.getcwd(),
            "explicit_session": None,
        }
    return {
        "configured": configured,
        "container": configured,
        "explicit_session": None,
    }


def _use_configured_root_directly(mode):
    configured = _configured_root_path()
    if mode != "create":
        return ""
    if SESSION_DIR_RE.match(os.path.basename(configured)):
        return configured
    return configured


def _session_path():
    return os.path.abspath(os.path.join(os.getcwd(), config.SESSION_FILE))


def _load_session_state():
    data = read_json_file(_session_path(), default={})
    return data if isinstance(data, dict) else {}


def _normalize_prompt_text(user_prompt):
    lower = (user_prompt or "").lower()
    lower = unicodedata.normalize("NFKD", lower)
    return "".join(ch for ch in lower if not unicodedata.combining(ch))


def _prompt_word_tokens(user_prompt):
    lower = _normalize_prompt_text(user_prompt)
    return re.findall(r"[a-z0-9_]+", lower)


def _rescue_mode():
    mode = str(getattr(config, "RESCUE_MODE", "OFF") or "OFF").strip().upper()
    if mode not in {"OFF", "ON", "ASK_BEFORE_RESCUE"}:
        return "OFF"
    return mode


def _required_b64_fields(user_prompt):
    lower = _normalize_prompt_text(user_prompt)
    required = []
    for key in ("content_b64", "old_b64", "new_b64"):
        if key in lower:
            required.append(key)
    return required


def _looks_like_base64_text(value):
    text = str(value or "").strip()
    if len(text) < 12:
        return False
    if len(text) % 4 != 0:
        return False
    if not re.fullmatch(r"[A-Za-z0-9+/=]+", text):
        return False
    try:
        raw = base64.b64decode(text, validate=True)
        decoded = raw.decode("utf-8")
    except Exception:
        return False
    markers = ("\n", "def ", "class ", "import ", "print(", "{", "}", ";", "<", ">")
    return any(marker in decoded for marker in markers)


def _is_likely_large_create_prompt(user_prompt):
    text = _normalize_prompt_text(user_prompt)
    if len(text) >= 700:
        return True
    if len(text) >= 420 and any(
        marker in text
        for marker in ("game", "website", "web app", "pygame", "tkinter", "flask", "fastapi", "full app", "full file")
    ):
        return True
    return False


def _is_effectively_empty_project_root(project_root):
    root = str(project_root or "").strip()
    if not root or not os.path.isdir(root):
        return True
    ignored_names = {".git", ".agent", "__pycache__"}
    ignored_ext = {".log", ".tmp"}
    try:
        for entry in os.scandir(root):
            name = entry.name.lower()
            if name in ignored_names:
                continue
            if entry.is_file() and os.path.splitext(name)[1] in ignored_ext:
                continue
            return False
    except Exception:
        return False
    return True


def _choose_initial_create_strategy(user_prompt, project_root=""):
    provider = str(config.PROVIDER or "").strip().lower()
    fresh_empty = _is_effectively_empty_project_root(project_root)
    prompt_size = len(_normalize_prompt_text(user_prompt))
    if fresh_empty:
        # Fresh local create should stay simple first; chunking is escalation-only.
        if provider == "lmstudio":
            if prompt_size >= 1800:
                return "chunked_rewrite", "very large prompt in fresh project"
            return "write_file", "fresh empty project default"
        if prompt_size >= 1400:
            return "chunked_rewrite", "large prompt size"
        return "write_file", "fresh empty project default"
    if _is_likely_large_create_prompt(user_prompt) and provider == "lmstudio":
        return "chunked_rewrite", "large-task heuristic"
    if prompt_size >= 1000:
        return "chunked_rewrite", "large prompt size"
    return "write_file", "default simple create"


def _is_large_write_action(action):
    if str(action.get("type", "")) != "write_file":
        return False
    args = action.get("args") if isinstance(action.get("args"), dict) else {}
    content = str(args.get("content", ""))
    content_b64 = str(args.get("content_b64", ""))
    return len(content) >= 700 or len(content_b64) >= 500


def _rescue_backend_available():
    return bool(config.OPENAI_RESCUE_ENABLED and str(config.OPENAI_API_KEY or "").strip())


def _can_use_rescue():
    return bool(_rescue_mode() == "ON" and _rescue_backend_available())


def _rescue_confirmable():
    return bool(_rescue_mode() in {"ON", "ASK_BEFORE_RESCUE"} and _rescue_backend_available())


def _rescue_suppressed_reason():
    mode = _rescue_mode()
    if mode == "OFF":
        return "automatic OpenAI fallback disabled"
    if mode == "ASK_BEFORE_RESCUE":
        return "ASK_BEFORE_RESCUE requires explicit confirmation"
    if not config.OPENAI_RESCUE_ENABLED:
        return "OPENAI_RESCUE_ENABLED is false"
    if not str(config.OPENAI_API_KEY or "").strip():
        return "OpenAI API key missing"
    return "rescue unavailable"


def _choose_task_profile(state, user_prompt, consecutive_parse_errors=0):
    text = _normalize_prompt_text(user_prompt)
    token_count = len(_prompt_word_tokens(user_prompt))
    runtime_lower = _normalize_prompt_text(state.last_runtime_error)
    repeated_failures = consecutive_parse_errors >= 2 or state.stuck_iterations >= 2
    no_progress_signal = state.stuck_iterations >= 1
    grounding_signal = any(marker in runtime_lower for marker in ("anchor", "traceback", "syntaxerror", "indentationerror"))

    if repeated_failures:
        if _can_use_rescue():
            return "rescue", "repeated parse failures or no progress"
        if state.mode == "patch":
            return "deep_patch", "repeated parse failures or no progress"
        return "standard_create", "repeated parse failures or no progress"

    if state.mode == "create":
        if state.task_shape in TRANSFORM_TASK_SHAPES:
            if state.large_file_mode in {"chunk", "strict_chunk"}:
                return "standard_create", "transform text-heavy task"
            if token_count <= 26 and len(text) <= 220:
                return "simple_create", "small transform/create task"
            return "standard_create", "transform copy/report task"
        if state.create_strategy == "chunked_rewrite" or _is_likely_large_create_prompt(user_prompt):
            return "standard_create", "large/code-heavy create task"
        if token_count <= 32 and len(text) <= 260:
            return "simple_create", "small create task"
        return "standard_create", "default create profile"

    if _normalize_patch_strategy(getattr(state, "patch_strategy", "")) in {"rewrite_existing_file", "chunked_rewrite_existing_file"}:
        return "deep_patch", f"patch strategy: {state.patch_strategy}"
    if grounding_signal:
        return "deep_patch", "patch grounding or runtime failure signal"
    if state.patch_phase in {"inspect_target", "verify_patch"}:
        return "standard_patch", f"patch phase: {state.patch_phase}"
    if token_count <= 30 and len(text) <= 300:
        return "simple_patch", "small focused patch"
    if no_progress_signal:
        return "deep_patch", "repeated no progress"
    return "standard_patch", "default patch profile"


def _route_for_task_profile(state, profile):
    base = str(state.base_provider or state.current_provider or config.PROVIDER).strip().lower()
    if base not in {"lmstudio", "openai"}:
        base = str(config.PROVIDER or "lmstudio").strip().lower() or "lmstudio"

    if state.current_provider.lower() == "openai" and state.rescue_handoff_count > 0 and _rescue_confirmable():
        return "rescue", "openai", "rescue handoff active"

    if profile == "rescue":
        if _can_use_rescue():
            return "rescue", "openai", "repeated failures"
        if _rescue_mode() == "ASK_BEFORE_RESCUE" and _rescue_backend_available():
            fallback_route = "deep" if state.mode == "patch" else "standard"
            return fallback_route, base, "rescue requires ASK_BEFORE_RESCUE confirmation"
        fallback_route = "deep" if state.mode == "patch" else "standard"
        return fallback_route, base, f"rescue unavailable: {_rescue_suppressed_reason()}"

    if profile in {"simple_create", "simple_patch"}:
        route = "local/default" if base == "lmstudio" else "standard"
        return route, base, "simple task"

    if profile in {"standard_create", "standard_patch"}:
        return "standard", base, "standard complexity"

    if profile == "deep_patch":
        return "deep", base, "complex patch"

    return "local/default", base, "default route"


def _is_syntax_like_failure(message):
    lower = _normalize_prompt_text(message)
    markers = (
        "syntaxerror",
        "indentationerror",
        "taberror",
        "invalid syntax",
        "expected an indented block",
        "unindent does not match",
    )
    return any(marker in lower for marker in markers)


def _patch_target_metrics(state):
    if state.mode != "patch" or not state.active_patch_target:
        return 0, 0
    content = _read_target_text(state.active_patch_target, state.active_project_root)
    if not content:
        return 0, 0
    return len(content), len(content.splitlines())


def _prefer_chunked_patch_rewrite(state):
    chars, lines = _patch_target_metrics(state)
    provider = str(state.current_provider or state.base_provider or config.PROVIDER or "").strip().lower()
    if chars >= 5000 or lines >= 220:
        return True
    if provider == "lmstudio" and (chars >= 2200 or lines >= 120):
        return True
    if state.task_profile in {"deep_patch", "rescue"} and (chars >= 1800 or lines >= 90):
        return True
    return False


def _normalize_patch_strategy(value):
    allowed = {"surgical_patch", "rewrite_existing_file", "chunked_rewrite_existing_file"}
    clean = str(value or "").strip().lower()
    if clean in allowed:
        return clean
    return "surgical_patch"


def _maybe_escalate_patch_strategy(state):
    if state.mode != "patch":
        return ""

    state.patch_strategy = _normalize_patch_strategy(state.patch_strategy)
    reason = ""
    if state.patch_strategy == "surgical_patch":
        if state.patch_verify_failure_streak >= 2 and state.patch_broad_patch_streak >= 1:
            reason = "repeated staged verify failures"
        elif state.patch_syntax_failure_streak >= 2 and state.patch_broad_patch_streak >= 1:
            reason = "syntax failure after broad patch"
        elif state.patch_anchor_failure_streak >= 2 and state.patch_failure_streak >= 2:
            reason = "repeated missing-anchor retries with no effective progress"
        elif state.patch_broad_patch_streak >= 2 and state.patch_failure_streak >= 2:
            reason = "repeated broad replace_block attempts"
        elif state.patch_hotspot_unavailable_streak >= 2 and state.patch_failure_streak >= 1:
            reason = "hotspots unavailable after failed patch attempts"
        if reason:
            state.patch_strategy = "chunked_rewrite_existing_file" if _prefer_chunked_patch_rewrite(state) else "rewrite_existing_file"
            state.patch_strategy_reason = reason
            state.patch_phase = "inspect_target"
            return reason

    if state.patch_strategy == "rewrite_existing_file":
        if (state.patch_failure_streak >= 2 or state.patch_verify_failure_streak >= 1) and _prefer_chunked_patch_rewrite(state):
            reason = "rewrite_existing_file remained unstable"
            state.patch_strategy = "chunked_rewrite_existing_file"
            state.patch_strategy_reason = reason
            state.patch_phase = "inspect_target"
            return reason

    return ""


def _list_session_dirs(container):
    if not os.path.isdir(container):
        return []
    items = []
    for name in os.listdir(container):
        full = os.path.join(container, name)
        match = SESSION_DIR_RE.match(name)
        if match and os.path.isdir(full):
            items.append((int(match.group(1)), full))
    items.sort()
    return [full for _, full in items]


def _choose_project_root(mode):
    info = _session_container_info()
    explicit_session = info["explicit_session"]
    configured = info["configured"]
    container = info["container"]
    direct_root = _use_configured_root_directly(mode)

    if explicit_session:
        if mode == "create":
            os.makedirs(explicit_session, exist_ok=True)
        if mode == "patch" and not os.path.isdir(explicit_session):
            raise Exception(f"Patch mode needs an existing project folder: {explicit_session}")
        return explicit_session

    if direct_root:
        os.makedirs(direct_root, exist_ok=True)
        return direct_root

    sessions = _list_session_dirs(container)
    if mode == "patch":
        if sessions:
            return sessions[-1]
        if os.path.isdir(configured) and any(os.scandir(configured)):
            return configured
        raise Exception(f"Patch mode needs an existing TEST-N folder inside: {container}")

    os.makedirs(container, exist_ok=True)
    next_index = 1
    if sessions:
        last_name = os.path.basename(sessions[-1])
        match = SESSION_DIR_RE.match(last_name)
        if match:
            next_index = int(match.group(1)) + 1
    active_root = os.path.join(container, f"{config.TEST_DIR_PREFIX}{next_index}")
    os.makedirs(active_root, exist_ok=True)
    return active_root


def _candidate_existing_roots():
    roots = []
    session_data = _load_session_state()
    session_root = str(session_data.get("project_root", "")).strip()
    if session_root and os.path.isdir(session_root):
        roots.append(session_root)

    info = _session_container_info()
    configured = info["configured"]
    if os.path.isdir(configured):
        roots.append(configured)

    for session_dir in reversed(_list_session_dirs(info["container"])):
        if os.path.isdir(session_dir):
            roots.append(session_dir)

    unique = []
    seen = set()
    for root in roots:
        norm = os.path.normcase(os.path.abspath(root))
        if norm in seen:
            continue
        seen.add(norm)
        unique.append(root)
    return unique


def _prompt_mentions_existing_file(user_prompt):
    tokens = _extract_prompt_target_candidates(user_prompt)
    if not tokens:
        return False

    for root in _candidate_existing_roots():
        for token in tokens:
            if _sanitize_target_token(token, root, patch_mode=True):
                return True
    return False


def _roots_have_existing_python_file():
    for root in _candidate_existing_roots():
        try:
            for _, _, filenames in os.walk(root):
                if any(name.lower().endswith(".py") for name in filenames):
                    return True
        except Exception:
            continue
    return False


def _looks_like_existing_feature_edit(user_prompt, words):
    text = _normalize_prompt_text(user_prompt)
    if any(hint in text for hint in EXISTING_EDIT_HINTS):
        return True
    feature_tokens = {
        "dodaj",
        "add",
        "pause",
        "esc",
        "menu",
        "opcje",
        "opcje",
        "feature",
        "counter",
        "score",
        "spawn",
        "enemy",
        "gracza",
        "gracz",
        "ruch",
        "ui",
    }
    context_tokens = {"gra", "gry", "grze", "game", "app", "mojej", "moja", "my", "existing", "current", "this"}
    return bool(words.intersection(feature_tokens) and words.intersection(context_tokens))


def _looks_like_in_place_patch_request(text_norm):
    in_place_markers = (
        "in place",
        "in-place",
        "w miejscu",
        "edytuj",
        "napraw",
        "zmien",
        "popraw",
        "replace text in",
        "replace in",
        "patch",
        "fix ",
        "modify ",
        "change ",
    )
    return any(marker in text_norm for marker in in_place_markers)


def _resolve_prompt_tokens_in_root(user_prompt, project_root, existing_only=False):
    rels = []
    seen = set()
    for token in _extract_prompt_target_candidates(user_prompt):
        clean = _sanitize_target_token(token, project_root, patch_mode=bool(existing_only))
        if not clean:
            continue
        if clean in seen:
            continue
        seen.add(clean)
        rels.append(clean)
    return rels


def _infer_output_files_from_prompt(user_prompt, project_root, source_files=None):
    source_files = set(source_files or [])
    outputs = []
    seen = set()
    for rel_path in _resolve_prompt_tokens_in_root(user_prompt, project_root, existing_only=False):
        if rel_path in source_files:
            continue
        if rel_path in seen:
            continue
        seen.add(rel_path)
        outputs.append(rel_path)
    return outputs


def detect_task_shape(user_prompt, selected_files="", project_context=None):
    text_norm = _normalize_prompt_text(user_prompt)
    words = set(_prompt_word_tokens(user_prompt))
    selected_files = str(selected_files or "").strip()
    project_context = project_context or {}
    roots = list(project_context.get("candidate_roots") or _candidate_existing_roots())
    existing_tokens = []
    all_tokens = []
    for root in roots[:3]:
        if not os.path.isdir(root):
            continue
        existing_tokens.extend(_resolve_prompt_tokens_in_root(user_prompt, root, existing_only=True))
        all_tokens.extend(_resolve_prompt_tokens_in_root(user_prompt, root, existing_only=False))
    existing_tokens = list(dict.fromkeys(existing_tokens))
    all_tokens = list(dict.fromkeys(all_tokens))
    has_patch_words = any(keyword in words for keyword in PATCH_KEYWORDS)
    has_create_words = any(keyword in words for keyword in CREATE_KEYWORDS)
    has_transform_words = any(hint in text_norm for hint in TEXT_TRANSFORM_HINTS)
    has_report_words = any(token in text_norm for token in ("report", "summary", "analysis", "data.txt", "raport", "podsumowanie"))
    has_copy_words = any(token in text_norm for token in ("copy", "kopia", "template", "szablon", "based on", "from "))
    has_output_words = any(token in text_norm for token in ("create", "new file", "output", "plik", "utworz", "stworz", "zapisz"))
    multiple_files = len(all_tokens) >= 2

    if selected_files:
        sanitized_selected = []
        for chunk in re.split(r"[,;\n]+", selected_files):
            token = chunk.strip()
            if token:
                sanitized_selected.append(token)
        if len(sanitized_selected) > 1:
            return "multi_file_patch", "PATCH_FILES selected multiple files"
        return "single_file_patch", "PATCH_FILES selected single file"

    if has_transform_words and multiple_files and (has_copy_words or has_report_words):
        return "transform_copy_task", "source + derived outputs requested"
    if has_patch_words and has_create_words and multiple_files and has_output_words:
        return "transform_copy_task", "prompt mixes replace/change with new output files"
    if has_report_words and not has_patch_words and (multiple_files or "create" in words or "stworz" in words):
        return "analysis_report_task", "analysis/report artifact request"
    if has_patch_words and existing_tokens and _looks_like_in_place_patch_request(text_norm):
        if len(existing_tokens) > 1:
            return "multi_file_patch", "in-place patch request against existing files"
        return "single_file_patch", "in-place patch request against existing file"
    if has_patch_words and existing_tokens and not has_create_words and not has_transform_words:
        if len(existing_tokens) > 1:
            return "multi_file_patch", "patch keywords with existing files"
        return "single_file_patch", "patch keywords with existing file"
    if has_create_words and (multiple_files or has_transform_words or has_report_words):
        return "generate_new_files_task", "explicit create/generate new files"
    if has_transform_words and existing_tokens:
        return "transform_copy_task", "transform wording with source file context"
    return "project_generation_task", "default project generation flow"


def _infer_mode_with_reason(user_prompt, task_shape="", task_shape_reason=""):
    mode_control = str(getattr(config, "MODE_CONTROL", "AUTO") or "AUTO").strip().upper()
    if mode_control == "FORCE_CREATE":
        return "create", "forced by MODE_CONTROL=FORCE_CREATE"
    if mode_control == "FORCE_PATCH":
        return "patch", "forced by MODE_CONTROL=FORCE_PATCH"

    patch_files_value = str(config.PATCH_FILES or "")
    if patch_files_value.strip():
        return "patch", "PATCH_FILES non-empty"

    if task_shape in PATCH_TASK_SHAPES:
        return "patch", f"task shape routed to patch: {task_shape} ({task_shape_reason or 'shape match'})"
    if task_shape in NON_PATCH_TASK_SHAPES:
        return "create", f"task shape routed to create: {task_shape} ({task_shape_reason or 'shape match'})"

    words = set(_prompt_word_tokens(user_prompt))
    matched_patch = next((keyword for keyword in PATCH_KEYWORDS if keyword in words), "")
    if matched_patch in {"update", "modify", "change", "improve"}:
        has_create_intent = any(keyword in words for keyword in CREATE_KEYWORDS)
        has_existing_signal = _prompt_mentions_existing_file(user_prompt) or (
            "dodaj" in words and _roots_have_existing_python_file() and _looks_like_existing_feature_edit(user_prompt, words)
        )
        strong_patch_signal = any(
            keyword in words
            for keyword in {
                "patch",
                "fix",
                "replace",
                "repair",
                "refactor",
                "debug",
                "edytuj",
                "napraw",
                "przerob",
                "zmien",
                "popraw",
                "usun",
            }
        )
        if has_create_intent and not has_existing_signal and not strong_patch_signal:
            matched_patch = ""
    if matched_patch:
        return "patch", f'patch keyword matched: "{matched_patch}"'

    if "dodaj" in words and _prompt_mentions_existing_file(user_prompt):
        return "patch", 'prompt references existing file with "dodaj"'
    if "dodaj" in words and _roots_have_existing_python_file() and _looks_like_existing_feature_edit(user_prompt, words):
        return "patch", 'feature request targets existing project context with "dodaj"'

    matched_create = next((keyword for keyword in CREATE_KEYWORDS if keyword in words), "")
    if matched_create:
        return "create", f'create keyword matched: "{matched_create}"'

    return "create", "default create"


def _infer_mode(user_prompt):
    mode, _ = _infer_mode_with_reason(user_prompt)
    return mode


def _compute_task_fingerprint(user_prompt, project_root, selected_files, task_shape):
    payload = {
        "prompt": " ".join(_prompt_word_tokens(user_prompt)),
        "project_root": os.path.normcase(os.path.abspath(project_root or _configured_root_path())),
        "patch_files": str(selected_files or "").strip(),
        "task_shape": str(task_shape or ""),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _classify_large_file_mode(state):
    if state.task_shape not in TRANSFORM_TASK_SHAPES:
        return "disabled"
    candidates = list(state.source_readonly_files or [])
    if not candidates:
        return "disabled"
    max_size = 0
    for rel_path in candidates:
        abs_path = os.path.abspath(os.path.join(state.active_project_root, rel_path))
        try:
            size = os.path.getsize(abs_path)
        except Exception:
            size = 0
        if size > max_size:
            max_size = size
    if max_size > LARGE_FILE_STRICT_THRESHOLD:
        return "strict_chunk"
    if max_size > LARGE_FILE_CHUNK_THRESHOLD:
        return "chunk"
    if max_size > LARGE_FILE_ENABLED_THRESHOLD:
        return "enabled"
    return "disabled"


def _build_transform_file_policy(state, user_prompt):
    state.source_readonly_files = []
    state.derived_allowed_files = []
    state.transform_primary_source = ""
    state.transform_source_hash = ""
    state.transform_phase = "transform_analyze"
    state.transform_analysis_complete = False
    if state.task_shape not in TRANSFORM_TASK_SHAPES:
        state.large_file_mode = "disabled"
        return

    source_files = []
    for rel in _resolve_prompt_tokens_in_root(user_prompt, state.active_project_root, existing_only=True):
        if rel not in source_files:
            source_files.append(rel)
    output_files = _infer_output_files_from_prompt(user_prompt, state.active_project_root, source_files=source_files)
    if not source_files:
        for rel in _resolve_prompt_tokens_in_root(user_prompt, state.active_project_root, existing_only=False):
            abs_path = os.path.abspath(os.path.join(state.active_project_root, rel))
            if os.path.exists(abs_path):
                source_files.append(rel)
                break
    state.source_readonly_files = source_files
    state.transform_primary_source = source_files[0] if source_files else ""
    if state.transform_primary_source:
        abs_source = os.path.abspath(os.path.join(state.active_project_root, state.transform_primary_source))
        try:
            with open(abs_source, "rb") as handle:
                state.transform_source_hash = hashlib.sha256(handle.read()).hexdigest()
        except Exception:
            state.transform_source_hash = ""
    state.derived_allowed_files = output_files
    state.large_file_mode = _classify_large_file_mode(state)


def _is_text_like_output(path):
    ext = os.path.splitext(str(path or ""))[1].lower()
    return ext in {
        ".txt",
        ".md",
        ".json",
        ".yaml",
        ".yml",
        ".csv",
        ".html",
        ".xml",
        ".py",
        ".js",
        ".ts",
        ".css",
        ".bat",
        ".cmd",
        ".ps1",
        ".sh",
    }


def _is_safe_transform_output(rel_path, state):
    rel = normalize_rel_path(rel_path or "")
    if not rel:
        return False
    if rel in (state.derived_allowed_files or []):
        return True
    if not _is_text_like_output(rel):
        return False
    lower = _normalize_prompt_text(rel)
    if any(marker in lower for marker in ("data", "report", "summary", "analysis", "template", "copy", "map", "szablon")):
        return True
    source = str(state.transform_primary_source or "")
    if source:
        src_stem = _normalize_prompt_text(os.path.splitext(os.path.basename(source))[0])
        dst_stem = _normalize_prompt_text(os.path.splitext(os.path.basename(rel))[0])
        if src_stem and src_stem in dst_stem and src_stem != dst_stem:
            return True
    return False


def _is_transform_mutation_blocked(action_type, rel_path, state):
    if state.mode != "create" or state.task_shape not in TRANSFORM_TASK_SHAPES:
        return False, ""
    mutating = {
        "write_file",
        "replace_in_file",
        "insert_before",
        "insert_after",
        "replace_block",
        "patch_lines",
        "begin_file_rewrite",
        "append_file_chunk",
        "finalize_file_rewrite",
    }
    if action_type not in mutating:
        return False, ""
    rel = normalize_rel_path(rel_path or "")
    if not rel:
        return False, ""
    if rel in (state.source_readonly_files or []):
        return True, f"SOURCE FILE POLICY VIOLATION: {rel} is read-only in {state.task_shape}"
    allowed = list(state.derived_allowed_files or [])
    if allowed and rel not in allowed and not _is_safe_transform_output(rel, state):
        return True, f"DERIVED FILE POLICY VIOLATION: {rel} is not an allowed transform output"
    return False, ""


def _transform_source_context(state):
    if state.task_shape not in TRANSFORM_TASK_SHAPES or not state.transform_primary_source:
        return "", ""
    rel_path = state.transform_primary_source
    abs_path = os.path.abspath(os.path.join(state.active_project_root, rel_path))
    try:
        with open(abs_path, "r", encoding="utf-8") as handle:
            content = handle.read()
    except Exception:
        return "", ""
    chars = len(content)
    lines = len(content.splitlines())
    summary = f"{rel_path} -> chars={chars}, lines={lines}"
    snippet_lines = 70
    if chars > LARGE_FILE_CHUNK_THRESHOLD:
        snippet_lines = 36
    elif chars > LARGE_FILE_ENABLED_THRESHOLD:
        snippet_lines = 52
    snippet = read_file_snippet(rel_path, project_root=state.active_project_root, max_lines=snippet_lines)
    return summary, snippet


def _required_transform_outputs(state):
    outputs = []
    for rel in (state.derived_allowed_files or state.target_files or []):
        clean = normalize_rel_path(rel)
        if clean and clean not in outputs:
            outputs.append(clean)
    return outputs


def _transform_outputs_exist(state):
    required = _required_transform_outputs(state)
    if not required:
        return False, []
    missing = []
    for rel in required:
        abs_path = os.path.abspath(os.path.join(state.active_project_root, rel))
        if not os.path.exists(abs_path):
            missing.append(rel)
    return len(missing) == 0, missing


def _placeholder_hits(text):
    raw = str(text or "")
    markers = [
        "PLACEHOLDER",
        "{{PLACEHOLDER",
        "[[PLACEHOLDER",
        "__PLACEHOLDER",
    ]
    return sum(1 for marker in markers if marker.lower() in raw.lower())


def _verify_transform_outputs(state):
    from contracts import Observation

    if state.task_shape != "transform_copy_task":
        return Observation(True, "TRANSFORM VERIFY SKIP", changed=False, details="not a transform_copy_task", tool="verify_transform")

    required = _required_transform_outputs(state)
    if not required:
        return Observation(False, "TRANSFORM VERIFY FAIL", changed=False, details="No required derived outputs inferred.", tool="verify_transform")

    missing = []
    bad = []
    for rel in required:
        abs_path = os.path.abspath(os.path.join(state.active_project_root, rel))
        if not os.path.exists(abs_path):
            missing.append(rel)
            continue
        try:
            with open(abs_path, "r", encoding="utf-8") as handle:
                content = handle.read()
        except Exception as exc:
            bad.append(f"{rel}: unreadable ({exc})")
            continue
        if _placeholder_hits(content) <= 0:
            bad.append(f"{rel}: no placeholder markers detected")

    source_changed = False
    if state.transform_primary_source and state.transform_source_hash:
        source_abs = os.path.abspath(os.path.join(state.active_project_root, state.transform_primary_source))
        try:
            with open(source_abs, "rb") as handle:
                current_hash = hashlib.sha256(handle.read()).hexdigest()
            source_changed = current_hash != state.transform_source_hash
        except Exception:
            source_changed = True

    if missing or bad or source_changed:
        details = []
        if missing:
            details.append("missing outputs: " + ", ".join(missing))
        if bad:
            details.append("content checks: " + "; ".join(bad))
        if source_changed:
            details.append(f"source changed unexpectedly: {state.transform_primary_source}")
        return Observation(
            False,
            "TRANSFORM VERIFY FAIL",
            changed=False,
            details=" | ".join(details),
            tool="verify_transform",
        )

    return Observation(
        True,
        "TRANSFORM VERIFY OK",
        changed=False,
        details="required outputs present; placeholder markers detected; source unchanged",
        tool="verify_transform",
    )


def _is_transform_plain_source_read(action, state):
    if state.task_shape != "transform_copy_task":
        return False
    if str(action.get("type") or "").strip() != "read_file":
        return False
    source = normalize_rel_path(state.transform_primary_source or "")
    if not source:
        return False
    args = action.get("args", {}) if isinstance(action.get("args"), dict) else {}
    path = normalize_rel_path(args.get("path", ""))
    if path != source:
        return False
    targeted = bool(
        args.get("section_id")
        or args.get("line_start")
        or args.get("line_end")
        or args.get("around_anchor")
        or args.get("query")
    )
    return not targeted


SHORTCODE_TEXT_BLOCK_RE = re.compile(r"\[fusion_text[^\]]*\](.*?)\[/fusion_text\]", re.IGNORECASE | re.DOTALL)
SHORTCODE_IMAGE_VALUE_RE = re.compile(
    r'((?:image|images|src|url|href)\s*=\s*")(.*?)(")',
    re.IGNORECASE | re.DOTALL,
)
SHORTCODE_MARKERS = ("[fusion_text", "[fusion_imageframe", "[fusion_images", "[fusion_content_box", "[fusion_checklist")


def _looks_like_shortcode_content(text):
    lower = _normalize_prompt_text(text or "")
    return any(marker in lower for marker in SHORTCODE_MARKERS)


def _deterministic_transform_copy(state):
    source_rel = normalize_rel_path(state.transform_primary_source or "")
    outputs = _required_transform_outputs(state)
    if not source_rel:
        return False, [], "missing transform source"
    if not outputs:
        return False, [], "missing derived outputs"

    source_abs = os.path.abspath(os.path.join(state.active_project_root, source_rel))
    try:
        with open(source_abs, "r", encoding="utf-8") as handle:
            source_text = handle.read()
    except Exception as exc:
        return False, [], f"source read failed: {exc}"

    if not _looks_like_shortcode_content(source_text):
        return False, [], "builtin deterministic transform expects shortcode content"

    records = []
    text_counter = 0
    picture_counter = 0

    def _replace_text_block(match):
        nonlocal text_counter
        original = match.group(1) or ""
        stripped = original.strip()
        if not stripped:
            return match.group(0)
        text_counter += 1
        placeholder = f"PLACEHOLDER_TEXT_{text_counter}"
        records.append(f"{placeholder}|fusion_text|{truncate_middle(stripped.replace(chr(10), ' '), 240)}")
        return match.group(0).replace(original, placeholder)

    transformed = SHORTCODE_TEXT_BLOCK_RE.sub(_replace_text_block, source_text)

    def _replace_image_attr(match):
        nonlocal picture_counter
        value = (match.group(2) or "").strip()
        if not value:
            return match.group(0)
        if "placeholder_picture_" in _normalize_prompt_text(value):
            return match.group(0)
        picture_counter += 1
        placeholder = f"PLACEHOLDER_PICTURE_{picture_counter}"
        records.append(f"{placeholder}|image_attr|{truncate_middle(value, 240)}")
        return f'{match.group(1)}{placeholder}{match.group(3)}'

    transformed = SHORTCODE_IMAGE_VALUE_RE.sub(_replace_image_attr, transformed)

    if text_counter == 0 and picture_counter == 0:
        return False, [], "no user-facing blocks detected for deterministic transform"

    data_lines = [
        f"source={source_rel}",
        f"text_placeholders={text_counter}",
        f"picture_placeholders={picture_counter}",
        "",
    ]
    data_lines.extend(records)
    data_text = "\n".join(data_lines).strip() + "\n"

    written = []
    for rel in outputs:
        if rel == source_rel:
            return False, written, "derived output points to source file"
        content = transformed if rel.lower().endswith("szablon2.txt") else data_text
        observation = write_file(
            rel,
            content,
            project_root=state.active_project_root,
            patch_mode=False,
            allow_create=True,
        )
        if not observation.ok:
            return False, written, observation.details or observation.summary
        written.append(rel)

    return True, written, ""


def _file_signature(project_root, rel_path):
    try:
        abs_path = os.path.abspath(os.path.join(project_root, normalize_rel_path(rel_path)))
        stat = os.stat(abs_path)
        return f"{int(stat.st_mtime_ns)}:{int(stat.st_size)}"
    except Exception:
        return ""


def _structured_large_read_observation(rel_path, project_root, large_mode="enabled"):
    abs_path = os.path.abspath(os.path.join(project_root, normalize_rel_path(rel_path)))
    try:
        with open(abs_path, "r", encoding="utf-8") as handle:
            content = handle.read()
    except Exception as exc:
        return "", {"summary": "", "details": f"READ CACHE ERROR: {exc}"}

    lines = content.splitlines()
    total_lines = len(lines)
    total_chars = len(content)
    if large_mode == "strict_chunk":
        section_size = 180
    elif large_mode == "chunk":
        section_size = 280
    else:
        section_size = 420

    sections = []
    idx = 1
    for start in range(0, total_lines, section_size):
        end = min(total_lines, start + section_size)
        preview = ""
        fusion_preview = ""
        for line in lines[start:end]:
            cleaned = line.strip()
            if cleaned:
                if not preview:
                    preview = truncate_middle(cleaned, 100)
                if "[fusion_" in cleaned.lower():
                    fusion_preview = truncate_middle(cleaned, 120)
                    break
        sections.append(
            {
                "id": f"S{idx}",
                "start_line": start + 1,
                "end_line": end,
                "preview": fusion_preview or preview or "(blank/markup-heavy section)",
            }
        )
        idx += 1
        if len(sections) >= 24:
            break

    checkpoints = [1, max(1, total_lines // 2), max(1, total_lines - 40)]
    snippets = []
    used = set()
    for center in checkpoints:
        if center in used:
            continue
        used.add(center)
        snippets.append(
            {
                "center": center,
                "snippet": read_file_snippet(rel_path, project_root=project_root, max_lines=30, around_line=center),
            }
        )

    detail_lines = [
        f"LARGE FILE TRANSPORT: {rel_path}",
        f"- total_chars={total_chars}, total_lines={total_lines}",
        f"- sections={len(sections)} (use section ids for targeted extraction)",
        "- section index:",
    ]
    for item in sections[:16]:
        detail_lines.append(
            f'  {item["id"]}: L{item["start_line"]}-L{item["end_line"]} preview="{item["preview"]}"'
        )
    detail_lines.append("- targeted excerpts:")
    for window in snippets:
        detail_lines.append(f'  window around L{window["center"]}:')
        detail_lines.append(truncate_middle(window["snippet"], 500))
    detail_lines.append(
        "- next step: use find_in_file/query and anchored extraction around relevant sections; avoid full-file reread."
    )

    try:
        stat = os.stat(abs_path)
        signature = f"{int(stat.st_mtime_ns)}:{int(stat.st_size)}"
    except Exception:
        signature = ""

    cache_entry = {
        "path": rel_path,
        "summary": f"LARGE FILE TRANSPORT READY {rel_path} sections={len(sections)}",
        "details": "\n".join(detail_lines),
        "chars": total_chars,
        "lines": total_lines,
        "sections": sections,
        "signature": signature,
    }
    return "\n".join(detail_lines), cache_entry


def _rotate_local_model(state, log=None):
    chain = []
    configured = getattr(config, "LOCAL_MODEL_CHAIN", None)
    if isinstance(configured, (list, tuple)):
        chain.extend([str(item).strip() for item in configured if str(item).strip()])
    configured_csv = str(getattr(config, "LMSTUDIO_MODEL_CHAIN", "") or "").strip()
    if configured_csv:
        chain.extend([chunk.strip() for chunk in configured_csv.split(",") if chunk.strip()])
    if not chain:
        if state.task_shape in TRANSFORM_TASK_SHAPES:
            chain = [config.LMSTUDIO_MODEL, "openai/gpt-oss-20b", "qwen/qwen3-coder-30b", "deepseek-coder"]
        else:
            chain = [config.LMSTUDIO_MODEL, "qwen/qwen3-coder-30b", "openai/gpt-oss-20b", "deepseek-coder"]
    unique_chain = []
    seen = set()
    for item in chain:
        norm = item.strip()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        unique_chain.append(norm)
    current = str(config.LMSTUDIO_MODEL or "").strip()
    if current in unique_chain:
        idx = unique_chain.index(current)
    else:
        unique_chain.insert(0, current)
        idx = 0
    if idx + 1 >= len(unique_chain):
        return False
    next_model = unique_chain[idx + 1]
    config.LMSTUDIO_MODEL = next_model
    if log:
        log(f"LOCAL FALLBACK STEP: alternate local model -> {next_model}")
    return True


def _attempt_local_fallback(state, history, log, reason):
    step = int(getattr(state, "local_fallback_step", 0) or 0)
    if step == 0:
        state.local_fallback_step = 1
        state.prompt_compaction_level = min(state.prompt_compaction_level + 1, 5)
        history.append(f"LOCAL FALLBACK STEP: prompt rewrite/compaction after: {reason}")
        log("LOCAL FALLBACK STEP: prompt rewrite / stronger grounding")
        return True
    if step == 1:
        state.local_fallback_step = 2
        if state.mode == "create":
            if state.create_strategy == "chunked_rewrite":
                state.create_strategy = "write_file"
                state.create_strategy_reason = "local fallback alternate strategy"
                state.create_phase = "fix_existing_file" if (state.last_written_files or state.created_files) else "initial_create"
                _reset_chunk_session(state, reason="local fallback strategy downgrade", clear_rewrite_state=False)
            else:
                state.create_phase = "fix_existing_file" if (state.last_written_files or state.created_files) else "initial_create"
        else:
            state.patch_phase = "inspect_target"
        history.append(f"LOCAL FALLBACK STEP: alternate strategy after: {reason}")
        log("LOCAL FALLBACK STEP: alternate strategy")
        return True
    if step == 2:
        state.local_fallback_step = 3
        if not getattr(config, "ALLOW_LOCAL_MODEL_SWITCH", False):
            if _rescue_mode() == "OFF" and str(state.current_provider or "").lower() == "lmstudio":
                message = "model switch blocked: rescue disabled, staying on selected local model"
            else:
                message = "model switch blocked: no_allowed_model_switch"
            history.append(f"LOCAL FALLBACK STEP BLOCKED: {message}")
            log(message)
            return False
        if state.current_provider.lower() == "lmstudio" and _rotate_local_model(state, log=log):
            history.append(f"LOCAL FALLBACK STEP: switched local model after: {reason}")
            return True
        history.append(f"LOCAL FALLBACK STEP: no alternate local model available after: {reason}")
        log("LOCAL FALLBACK STEP: no alternate local model available")
        return True
    if step == 3:
        state.local_fallback_step = 4
        if state.task_shape in TRANSFORM_TASK_SHAPES:
            state.create_strategy = "write_file"
            state.create_phase = "initial_create"
            history.append(
                "LOCAL FALLBACK STEP: deterministic transform mode. "
                "Use read-only source policy, extract blocks incrementally, then write derived artifacts."
            )
            log("LOCAL FALLBACK STEP: deterministic transform path")
            return True
    return False


def _find_by_basename(project_root, basename):
    basename = os.path.basename(basename)
    matches = []
    for dirpath, _, filenames in os.walk(project_root):
        for filename in filenames:
            if filename == basename:
                matches.append(os.path.join(dirpath, filename))
    return matches


def _sanitize_target_token(token, project_root, patch_mode):
    token = str(token or "").strip().strip('"').strip("'").strip("`").strip("[](){}")
    token = token.splitlines()[0].strip().rstrip(".,:;")
    if not token:
        return ""
    if token.lower() in BLOCKED_TARGET_TOKENS:
        return ""
    if "traceback" in token.lower():
        return ""
    token = token.replace("/", os.sep).replace("\\", os.sep)

    if os.path.isabs(token):
        abs_candidate = os.path.abspath(token)
        if not is_subpath(project_root, abs_candidate):
            return ""
        if patch_mode and not os.path.exists(abs_candidate):
            return ""
        return os.path.relpath(abs_candidate, project_root)

    rel_candidate = normalize_rel_path(token)
    abs_candidate = os.path.abspath(os.path.join(project_root, rel_candidate))
    if os.path.exists(abs_candidate):
        return os.path.relpath(abs_candidate, project_root)

    if os.sep not in rel_candidate:
        matches = _find_by_basename(project_root, rel_candidate)
        if len(matches) == 1:
            return os.path.relpath(matches[0], project_root)
        if len(matches) > 1:
            return ""

    if patch_mode:
        return ""

    if not is_subpath(project_root, abs_candidate):
        return ""
    return rel_candidate


def _extract_prompt_target_candidates(user_prompt):
    return FILE_TOKEN_RE.findall(user_prompt or "")


def _sanitize_target_files(user_prompt, project_root, patch_mode, fallback_target=""):
    candidates = []
    if config.PATCH_FILES.strip():
        for chunk in re.split(r"[,;\n]+", config.PATCH_FILES):
            candidates.append(chunk)
    candidates.extend(_extract_prompt_target_candidates(user_prompt))

    sanitized = []
    seen = set()
    for candidate in candidates:
        clean = _sanitize_target_token(candidate, project_root, patch_mode=patch_mode)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        sanitized.append(clean)

    if patch_mode and not sanitized and fallback_target:
        sanitized = [fallback_target]
    return sanitized


def _prefer_last_sensible_py(project_root, last_written_files=None):
    for item in reversed(last_written_files or []):
        if str(item).lower().endswith(".py"):
            abs_path = os.path.abspath(os.path.join(project_root, item))
            if os.path.exists(abs_path):
                return item

    best = ""
    best_mtime = -1.0
    for dirpath, _, filenames in os.walk(project_root):
        for filename in filenames:
            if filename.endswith(".py"):
                full = os.path.join(dirpath, filename)
                mtime = os.path.getmtime(full)
                if mtime > best_mtime:
                    best_mtime = mtime
                    best = os.path.relpath(full, project_root)
    if best:
        return best

    for dirpath, _, filenames in os.walk(project_root):
        for filename in filenames:
            full = os.path.join(dirpath, filename)
            return os.path.relpath(full, project_root)
    return "app.py"


def _append_unique(items, value):
    if value and value not in items:
        items.append(value)


def _soft_reset_runtime_state(state):
    state.last_runtime_error = ""
    state.last_plan_fingerprint = ""
    state.stuck_iterations = 0
    state.duplicate_action_cache = {}
    state.prompt_compaction_level = 0
    state.rescue_handoff_count = 0
    state.progress_happened = False
    state.last_useful_observation_summary = ""
    state.patch_strategy = "surgical_patch"
    state.patch_strategy_reason = ""
    state.patch_failure_streak = 0
    state.patch_exact_snippet = ""
    state.patch_hotspot_label = ""
    state.patch_task_intent = ""
    state.patch_hotspot_candidates = []
    state.patch_hotspot_primary_index = 0
    state.patch_hotspot_secondary_index = 1
    state.patch_hotspot_fail_counts = {}
    state.patch_hotspot_last_fail_reason = ""
    state.patch_stale_error_detected = False
    state.patch_stale_error_reason = ""
    state.patch_replace_miss_streak = 0
    state.patch_verify_failure_streak = 0
    state.patch_syntax_failure_streak = 0
    state.patch_anchor_failure_streak = 0
    state.patch_broad_patch_streak = 0
    state.patch_hotspot_unavailable_streak = 0
    state.chunk_session_open = False
    state.chunk_target_path = ""
    state.chunk_expected_parts = 0
    state.chunk_received_parts = 0
    state.chunk_missing_parts = []
    state.chunk_finalize_pending = False
    state.chunk_protocol_violation_streak = 0
    state.local_fallback_step = 0
    state.no_progress_streak = 0
    state.large_read_cache = {}
    state.transform_last_verify_ok = False
    state.transform_source_read_seen = False
    state.transform_no_material_progress_streak = 0
    state.transform_phase = "transform_analyze"
    state.transform_analysis_complete = False


def _hydrate_state_from_session(state, session_data):
    if not session_data:
        return

    session_root = str(session_data.get("project_root", "")).strip()
    configured_root = _explicit_configured_root_path()
    explicit_gui_root = bool(configured_root)
    if not explicit_gui_root and state.mode != "create" and session_root and os.path.isdir(session_root):
        state.active_project_root = session_root

    state.touched_files = list(session_data.get("touched_files") or [])
    state.created_files = list(session_data.get("created_files") or [])
    state.last_written_files = list(session_data.get("last_written_files") or [])
    state.iteration_count = int(session_data.get("iteration_count") or 0)
    state.last_useful_observation_summary = str(session_data.get("last_useful_observation_summary") or "")
    state.patch_strategy = _normalize_patch_strategy(session_data.get("patch_strategy", state.patch_strategy))
    state.patch_strategy_reason = str(session_data.get("patch_strategy_reason") or "")
    state.patch_failure_streak = int(session_data.get("patch_failure_streak") or 0)
    state.patch_exact_snippet = str(session_data.get("patch_exact_snippet") or "")
    state.patch_hotspot_label = str(session_data.get("patch_hotspot_label") or "")
    state.patch_task_intent = str(session_data.get("patch_task_intent") or "")
    state.patch_hotspot_candidates = list(session_data.get("patch_hotspot_candidates") or [])
    state.patch_hotspot_primary_index = int(session_data.get("patch_hotspot_primary_index") or 0)
    state.patch_hotspot_secondary_index = int(session_data.get("patch_hotspot_secondary_index") or 1)
    cached_fail_counts = session_data.get("patch_hotspot_fail_counts") or {}
    state.patch_hotspot_fail_counts = dict(cached_fail_counts) if isinstance(cached_fail_counts, dict) else {}
    state.patch_hotspot_last_fail_reason = str(session_data.get("patch_hotspot_last_fail_reason") or "")
    state.patch_stale_error_detected = bool(session_data.get("patch_stale_error_detected") or False)
    state.patch_stale_error_reason = str(session_data.get("patch_stale_error_reason") or "")
    state.patch_replace_miss_streak = int(session_data.get("patch_replace_miss_streak") or 0)
    state.patch_verify_failure_streak = int(session_data.get("patch_verify_failure_streak") or 0)
    state.patch_syntax_failure_streak = int(session_data.get("patch_syntax_failure_streak") or 0)
    state.patch_anchor_failure_streak = int(session_data.get("patch_anchor_failure_streak") or 0)
    state.patch_broad_patch_streak = int(session_data.get("patch_broad_patch_streak") or 0)
    state.patch_hotspot_unavailable_streak = int(session_data.get("patch_hotspot_unavailable_streak") or 0)
    state.chunk_session_open = bool(session_data.get("chunk_session_open") or False)
    state.chunk_target_path = normalize_rel_path(session_data.get("chunk_target_path") or "")
    state.chunk_expected_parts = int(session_data.get("chunk_expected_parts") or 0)
    state.chunk_received_parts = int(session_data.get("chunk_received_parts") or 0)
    state.chunk_missing_parts = list(session_data.get("chunk_missing_parts") or [])
    state.chunk_finalize_pending = bool(session_data.get("chunk_finalize_pending") or False)
    state.chunk_protocol_violation_streak = int(session_data.get("chunk_protocol_violation_streak") or 0)
    state.local_fallback_step = int(session_data.get("local_fallback_step") or 0)
    state.no_progress_streak = int(session_data.get("no_progress_streak") or 0)
    state.task_shape = str(session_data.get("task_shape") or state.task_shape)
    state.task_shape_reason = str(session_data.get("task_shape_reason") or state.task_shape_reason)
    state.task_fingerprint = str(session_data.get("task_fingerprint") or state.task_fingerprint)
    state.source_readonly_files = list(session_data.get("source_readonly_files") or state.source_readonly_files)
    state.derived_allowed_files = list(session_data.get("derived_allowed_files") or state.derived_allowed_files)
    state.transform_primary_source = str(session_data.get("transform_primary_source") or state.transform_primary_source)
    state.large_file_mode = str(session_data.get("large_file_mode") or state.large_file_mode)
    cached_large_reads = session_data.get("large_read_cache") or {}
    state.large_read_cache = dict(cached_large_reads) if isinstance(cached_large_reads, dict) else {}
    state.transform_last_verify_ok = bool(session_data.get("transform_last_verify_ok") or False)
    state.transform_source_hash = str(session_data.get("transform_source_hash") or state.transform_source_hash)
    state.transform_source_read_seen = bool(session_data.get("transform_source_read_seen") or False)
    state.transform_no_material_progress_streak = int(session_data.get("transform_no_material_progress_streak") or 0)
    state.transform_phase = str(session_data.get("transform_phase") or state.transform_phase)
    state.transform_analysis_complete = bool(session_data.get("transform_analysis_complete") or False)

    if not state.prompt_changed:
        state.last_runtime_error = str(session_data.get("last_runtime_error") or "")
        state.last_plan_fingerprint = str(session_data.get("last_plan_fingerprint") or "")
        state.stuck_iterations = int(session_data.get("stuck_iterations") or 0)
        cached = session_data.get("duplicate_action_cache") or {}
        if isinstance(cached, dict):
            state.duplicate_action_cache = dict(cached)
    else:
        _soft_reset_runtime_state(state)


def _save_session_state(state):
    test_folder = os.path.basename(state.active_project_root)
    if not SESSION_DIR_RE.match(test_folder):
        test_folder = ""

    payload = {
        "goal": state.goal,
        "prompt": state.goal,
        "mode": state.mode,
        "mode_reason": state.mode_reason,
        "task_kind": state.mode,
        "project_root": state.active_project_root,
        "target_files": list(state.target_files),
        "active_patch_target": state.active_patch_target,
        "iteration_count": state.iteration_count,
        "touched_files": list(state.touched_files),
        "created_files": list(state.created_files),
        "last_written_files": list(state.last_written_files),
        "action_hashes": list(state.duplicate_action_cache.keys()),
        "duplicate_action_cache": dict(state.duplicate_action_cache),
        "stuck_iterations": state.stuck_iterations,
        "last_plan_fingerprint": state.last_plan_fingerprint,
        "last_runtime_error": state.last_runtime_error,
        "last_useful_observation_summary": state.last_useful_observation_summary,
        "current_test_folder": test_folder,
        "create_strategy": state.create_strategy,
        "create_phase": state.create_phase,
        "create_strategy_reason": state.create_strategy_reason,
        "create_full_write_streak": state.create_full_write_streak,
        "last_created_main_file": state.last_created_main_file,
        "patch_strategy": state.patch_strategy,
        "patch_strategy_reason": state.patch_strategy_reason,
        "patch_failure_streak": state.patch_failure_streak,
        "patch_exact_snippet": state.patch_exact_snippet,
        "patch_hotspot_label": state.patch_hotspot_label,
        "patch_task_intent": state.patch_task_intent,
        "patch_hotspot_candidates": list(state.patch_hotspot_candidates),
        "patch_hotspot_primary_index": state.patch_hotspot_primary_index,
        "patch_hotspot_secondary_index": state.patch_hotspot_secondary_index,
        "patch_hotspot_fail_counts": dict(state.patch_hotspot_fail_counts),
        "patch_hotspot_last_fail_reason": state.patch_hotspot_last_fail_reason,
        "patch_stale_error_detected": state.patch_stale_error_detected,
        "patch_stale_error_reason": state.patch_stale_error_reason,
        "patch_replace_miss_streak": state.patch_replace_miss_streak,
        "patch_verify_failure_streak": state.patch_verify_failure_streak,
        "patch_syntax_failure_streak": state.patch_syntax_failure_streak,
        "patch_anchor_failure_streak": state.patch_anchor_failure_streak,
        "patch_broad_patch_streak": state.patch_broad_patch_streak,
        "patch_hotspot_unavailable_streak": state.patch_hotspot_unavailable_streak,
        "chunk_session_open": state.chunk_session_open,
        "chunk_target_path": state.chunk_target_path,
        "chunk_expected_parts": state.chunk_expected_parts,
        "chunk_received_parts": state.chunk_received_parts,
        "chunk_missing_parts": list(state.chunk_missing_parts),
        "chunk_finalize_pending": state.chunk_finalize_pending,
        "chunk_protocol_violation_streak": state.chunk_protocol_violation_streak,
        "task_profile": state.task_profile,
        "model_route": state.model_route,
        "route_reason": state.route_reason,
        "task_shape": state.task_shape,
        "task_shape_reason": state.task_shape_reason,
        "task_fingerprint": state.task_fingerprint,
        "source_readonly_files": list(state.source_readonly_files),
        "derived_allowed_files": list(state.derived_allowed_files),
        "transform_primary_source": state.transform_primary_source,
        "large_file_mode": state.large_file_mode,
        "local_fallback_step": state.local_fallback_step,
        "no_progress_streak": state.no_progress_streak,
        "large_read_cache": dict(state.large_read_cache),
        "transform_last_verify_ok": state.transform_last_verify_ok,
        "transform_source_hash": state.transform_source_hash,
        "transform_source_read_seen": state.transform_source_read_seen,
        "transform_no_material_progress_streak": state.transform_no_material_progress_streak,
        "transform_phase": state.transform_phase,
        "transform_analysis_complete": state.transform_analysis_complete,
    }
    write_json_file(_session_path(), payload)


def _clear_runtime_session(project_root=None):
    targets = [_session_path(), os.path.join(os.getcwd(), config.RAW_LOG_FILE)]
    if project_root:
        root = os.path.abspath(project_root)
        targets.extend(
            [
                os.path.join(root, REWRITE_STATE_DIR),
                os.path.join(root, ".agent", "iteration_stage"),
            ]
        )
    for target in targets:
        if not target:
            continue
        try:
            if os.path.isdir(target):
                shutil.rmtree(target, ignore_errors=True)
            elif os.path.exists(target):
                os.remove(target)
        except Exception:
            pass


def clear_runtime_session(project_root=None, *args, **kwargs):
    _clear_runtime_session(project_root=project_root)


def _compact_log_message(message):
    text = str(message or "").strip()
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) <= 1:
        if len(text) <= 260:
            return text
        return truncate_middle(text, 260)
    if text.upper().count("SECTION") > 12:
        return f"output collapsed: {len(lines)} lines, repeated token SECTION"
    unique = []
    seen = set()
    for line in lines:
        if line in seen:
            continue
        seen.add(line)
        unique.append(line)
        if len(unique) >= 3:
            break
    if len(set(lines[: min(len(lines), 12)])) == 1:
        return f"{lines[0]} [same event repeated x{len(lines)}]"
    if len(unique) == 1:
        return f"{unique[0]} [collapsed: {len(lines)} lines, {len(text)} chars]"
    return f"{unique[0]} ... [collapsed: {len(lines)} lines, {len(text)} chars]"


def _build_state(user_prompt):
    session_data = _load_session_state()
    project_context = {"candidate_roots": _candidate_existing_roots()}
    task_shape, task_shape_reason = detect_task_shape(
        user_prompt,
        selected_files=str(config.PATCH_FILES or ""),
        project_context=project_context,
    )
    mode, mode_reason = _infer_mode_with_reason(
        user_prompt,
        task_shape=task_shape,
        task_shape_reason=task_shape_reason,
    )
    previous_prompt = str(session_data.get("prompt") or session_data.get("goal") or "")
    previous_fingerprint = str(session_data.get("task_fingerprint") or "").strip()
    session_root = str(session_data.get("project_root", "")).strip()
    configured_root = _explicit_configured_root_path()
    explicit_gui_root = bool(configured_root)
    if explicit_gui_root and (mode == "create" or os.path.isdir(configured_root)):
        active_project_root = configured_root
    elif mode != "create" and session_root and os.path.isdir(session_root):
        active_project_root = session_root
    else:
        active_project_root = _choose_project_root(mode)
    task_fingerprint = _compute_task_fingerprint(
        user_prompt,
        active_project_root,
        str(config.PATCH_FILES or ""),
        task_shape,
    )
    prompt_changed = bool(previous_prompt and previous_prompt.strip() != (user_prompt or "").strip())
    task_changed = bool(previous_fingerprint and previous_fingerprint != task_fingerprint)
    state = PipelineState(
        goal=user_prompt,
        mode=mode,
        active_project_root=active_project_root,
        current_provider=config.PROVIDER,
        base_provider=config.PROVIDER,
        mode_reason=mode_reason,
        prompt_changed=(prompt_changed or task_changed),
        required_b64_fields=_required_b64_fields(user_prompt),
        task_shape=task_shape,
        task_shape_reason=task_shape_reason,
        task_fingerprint=task_fingerprint,
    )
    if mode == "create":
        if task_shape in TRANSFORM_TASK_SHAPES:
            strategy, strategy_reason = "write_file", "transform/analysis task default"
        else:
            strategy, strategy_reason = _choose_initial_create_strategy(user_prompt, active_project_root)
        state.create_strategy = strategy
        state.create_strategy_reason = strategy_reason
        state.create_phase = "chunk_begin" if strategy == "chunked_rewrite" else "initial_create"
    _hydrate_state_from_session(state, session_data)
    state.task_shape = task_shape
    state.task_shape_reason = task_shape_reason
    state.task_fingerprint = task_fingerprint
    if task_changed:
        _soft_reset_runtime_state(state)
        state.patch_task_intent = ""
        state.patch_hotspot_label = ""
        state.patch_hotspot_candidates = []
        state.patch_exact_snippet = ""
    config.ACTIVE_PROJECT_ROOT = state.active_project_root
    _build_transform_file_policy(state, user_prompt)

    fallback_target = _prefer_last_sensible_py(active_project_root)
    target_files = _sanitize_target_files(
        user_prompt,
        active_project_root,
        patch_mode=(mode == "patch"),
        fallback_target=fallback_target if mode == "patch" else "",
    )

    if mode == "patch":
        active_target = target_files[0] if target_files else fallback_target
        if not target_files:
            session_target = str(session_data.get("active_patch_target") or "").strip()
            if session_target:
                active_target = _sanitize_target_token(session_target, active_project_root, patch_mode=True) or active_target
        state.active_patch_target = active_target
        state.target_files = [active_target]
        state.expected_file_count = 1
        state.single_file_task = True
        state.patch_phase = "inspect_target"
        state.patch_strategy = _normalize_patch_strategy(state.patch_strategy)
        if not state.patch_strategy:
            state.patch_strategy = "surgical_patch"
    else:
        if state.task_shape in TRANSFORM_TASK_SHAPES and state.derived_allowed_files:
            state.target_files = list(state.derived_allowed_files)
        else:
            state.target_files = target_files
        state.expected_file_count = max(1, len(state.target_files) or 1)
        state.single_file_task = len(state.target_files) <= 1

    return state


def _prompt_budget(level):
    snippet_lines = max(20, config.PATCH_SNIPPET_LINES // (2 ** level))
    history_items = max(1, config.PROMPT_HISTORY_ITEMS - level * 2)
    history_chars = max(400, config.PROMPT_HISTORY_CHARS // (2 ** level))
    return snippet_lines, history_items, history_chars


def _compact_history(history, level):
    _, history_items, history_chars = _prompt_budget(level)
    items = history[-history_items:] if history else []
    joined = "\n".join(items)
    return truncate_middle(joined, history_chars)


def _read_target_text(path, project_root):
    try:
        abs_path = os.path.abspath(os.path.join(project_root, path))
        with open(abs_path, "r", encoding="utf-8") as handle:
            return handle.read()
    except Exception:
        return ""


def _python_outline_items(content):
    try:
        tree = ast.parse(content or "")
    except Exception:
        return []

    items = []
    lines = (content or "").splitlines()
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            header = lines[node.lineno - 1].strip() if 0 < node.lineno <= len(lines) else f"class {node.name}"
            items.append((node.lineno, header))
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    child_header = lines[child.lineno - 1].strip() if 0 < child.lineno <= len(lines) else f"def {child.name}"
                    items.append((child.lineno, child_header))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            header = lines[node.lineno - 1].strip() if 0 < node.lineno <= len(lines) else f"def {node.name}"
            items.append((node.lineno, header))
    return items


def _generic_outline_items(content):
    items = []
    for index, raw_line in enumerate((content or "").splitlines(), 1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        if len(stripped) > 120:
            continue
        if stripped.startswith(("#", "##", "###", "[", "{", "<section", "<div", "<main", "<header", "<footer", "function ", "const ", "let ", "var ", "export ", ".",
                               "@", "body", "if ", "for ", "while ")):
            items.append((index, stripped))
            continue
        if re.match(r"^[A-Za-z0-9_.-]+\s*[:={]", stripped):
            items.append((index, stripped))
            continue
    return items


def _discover_file_outline(path, project_root, max_items=18):
    content = _read_target_text(path, project_root)
    if not content:
        return []
    extension = os.path.splitext(str(path))[1].lower()
    if extension == ".py":
        items = _python_outline_items(content)
    else:
        items = _generic_outline_items(content)
    unique = []
    seen = set()
    for line_no, text in items:
        key = (line_no, text)
        if key in seen:
            continue
        seen.add(key)
        unique.append((line_no, text))
        if len(unique) >= max_items:
            break
    return unique


def _discover_real_anchors(path, project_root, max_items=12):
    outline = _discover_file_outline(path, project_root, max_items=max_items)
    anchors = []
    seen = set()
    for _, text in outline:
        if text in seen:
            continue
        seen.add(text)
        anchors.append(text)
        if len(anchors) >= max_items:
            break
    return anchors


def _keyword_tokens(text):
    lower = _normalize_prompt_text(text)
    return [token for token in re.findall(r"[a-zA-Z_]{3,}", lower) if token not in {"the", "and", "for", "with", "this", "that", "plik", "file"}]


def _focused_patch_snippets(path, project_root, user_prompt, max_lines):
    content = _read_target_text(path, project_root)
    if not content:
        return "(snippet unavailable)"
    lines = content.splitlines()
    tokens = _keyword_tokens(user_prompt)
    hit_lines = []
    for index, line in enumerate(lines, 1):
        lowered = _normalize_prompt_text(line)
        if any(token in lowered for token in tokens):
            hit_lines.append(index)
        if len(hit_lines) >= 2:
            break

    outline = _discover_file_outline(path, project_root, max_items=8)
    if not hit_lines and outline:
        hit_lines = [line_no for line_no, _ in outline[:2]]

    snippets = []
    seen = set()
    for line_no in hit_lines[:2]:
        snippet = read_file_snippet(path, project_root=project_root, max_lines=max(12, max_lines // 2), around_line=line_no)
        if snippet not in seen:
            seen.add(snippet)
            snippets.append(snippet)

    if not snippets:
        return read_file_snippet(path, project_root=project_root, max_lines=max_lines)
    return "\n\n".join(snippets)


def _grounded_patch_retry_context(path, project_root, user_prompt):
    outline_items = _discover_file_outline(path, project_root, max_items=10)
    anchor_items = _discover_real_anchors(path, project_root, max_items=8)
    snippet = _focused_patch_snippets(path, project_root, user_prompt, max(30, config.PATCH_SNIPPET_LINES))
    parts = [
        "Real file outline:",
        "\n".join(f"- L{line_no}: {text}" for line_no, text in outline_items) or "(outline unavailable)",
        "",
        "Real anchors from file:",
        "\n".join(f'- "{text}"' for text in anchor_items) or "(anchors unavailable)",
        "",
        "Focused snippets:",
        snippet or "(snippet unavailable)",
    ]
    return "\n".join(parts)


def _extract_error_like_text(user_prompt, state, history=None):
    bits = []
    prompt_text = str(user_prompt or "")
    if any(marker in _normalize_prompt_text(prompt_text) for marker in ("traceback", "error", "exception", "syntaxerror", "indentationerror")):
        bits.append(prompt_text)
    runtime_text = str(state.last_runtime_error or "")
    if runtime_text:
        bits.append(runtime_text)
    for item in (history or [])[-4:]:
        text = str(item or "")
        lowered = _normalize_prompt_text(text)
        if any(marker in lowered for marker in ("traceback", "error", "exception", "syntaxerror", "indentationerror")):
            bits.append(text)
    return "\n".join(bits).strip()


def _extract_error_symbols(error_text):
    if not error_text:
        return []
    stop_words = {
        "traceback",
        "line",
        "file",
        "error",
        "exception",
        "runtime",
        "recent",
        "call",
        "last",
        "most",
        "none",
        "true",
        "false",
        "nameerror",
        "typeerror",
        "valueerror",
        "syntaxerror",
        "indentationerror",
        "unboundlocalerror",
        "attributeerror",
        "keyerror",
        "indexerror",
    }
    raw = []
    raw.extend(re.findall(r"'([A-Za-z_][A-Za-z0-9_]*)'", error_text))
    raw.extend(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"', error_text))
    raw.extend(re.findall(r"\bin\s+([A-Za-z_][A-Za-z0-9_]*)\b", error_text))
    if not raw:
        fallback_tokens = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\b", _normalize_prompt_text(error_text))
        raw.extend(token for token in fallback_tokens if "_" in token)
    symbols = []
    seen = set()
    for token in raw:
        norm = _normalize_prompt_text(token).strip()
        if len(norm) < 3 or norm in stop_words or norm in seen:
            continue
        seen.add(norm)
        symbols.append(norm)
        if len(symbols) >= 24:
            break
    return symbols


def _detect_stale_error_context(path, project_root, user_prompt, state, history=None):
    error_text = _extract_error_like_text(user_prompt, state, history=history)
    if not error_text:
        return False, ""

    snapshot = _normalize_prompt_text(_read_target_text(path, project_root))
    if not snapshot:
        return False, ""

    symbols = _extract_error_symbols(error_text)
    if not symbols:
        return False, ""

    outlined = " ".join(
        _normalize_prompt_text(item_text) for _, item_text in _discover_file_outline(path, project_root, max_items=24)
    )
    anchor_pool = " ".join(_normalize_prompt_text(anchor) for anchor in _discover_real_anchors(path, project_root, max_items=16))
    search_space = " ".join([snapshot, outlined, anchor_pool]).strip()
    if not search_space:
        return False, ""

    present = [symbol for symbol in symbols if symbol in search_space]
    absent = [symbol for symbol in symbols if symbol not in search_space]
    has_error_markers = any(
        marker in _normalize_prompt_text(error_text)
        for marker in ("traceback", "error", "exception", "syntaxerror", "indentationerror", "unboundlocalerror")
    )
    if has_error_markers and absent and not present:
        preview = ", ".join(absent[:5])
        return True, f"symbol/block not present in current snapshot ({preview})"
    if has_error_markers and len(absent) >= 4 and len(present) <= 1:
        preview = ", ".join(absent[:5])
        return True, f"traceback context poorly aligned with current snapshot ({preview})"
    return False, ""


def _prune_traceback_lines(text):
    rows = []
    for row in str(text or "").splitlines():
        lowered = _normalize_prompt_text(row)
        if any(marker in lowered for marker in ("traceback", "error", "exception", "file \"", "line ")):
            continue
        rows.append(row)
    return "\n".join(rows)


def _patch_focus_tokens(user_prompt, state, history=None):
    history = history or []
    prompt_source = user_prompt or ""
    if getattr(state, "patch_stale_error_detected", False):
        prompt_source = _prune_traceback_lines(prompt_source)
    text_bits = [prompt_source]
    if state.last_runtime_error and not getattr(state, "patch_stale_error_detected", False):
        text_bits.append(state.last_runtime_error)
    if state.last_useful_observation_summary:
        text_bits.append(state.last_useful_observation_summary)
    if history and not getattr(state, "patch_stale_error_detected", False):
        text_bits.append("\n".join(history[-3:]))
    tokens = _keyword_tokens(" ".join(text_bits))
    unique = []
    seen = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        unique.append(token)
        if len(unique) >= 14:
            break
    return unique


def _behavior_patch_signals(user_prompt):
    lower = _normalize_prompt_text(user_prompt)
    buckets = []
    if any(token in lower for token in ("esc", "pause", "paused")):
        buckets.append("pause/esc")
    if any(token in lower for token in ("keyboard", "input", "keypress", "event")):
        buckets.append("event loop / keyboard handling")
    if any(token in lower for token in ("game loop", "restart", "game over", "score", "shoot", "enemy", "movement")):
        buckets.append("gameplay loop")
    return {
        "active": bool(buckets),
        "buckets": buckets,
    }


def _is_helper_like_hotspot(text):
    lowered = _normalize_prompt_text(text)
    helper_markers = ("clamp", "helper", "util", "normalize", "lerp", "math", "vector", "bounds")
    return any(marker in lowered for marker in helper_markers)


def _derive_patch_task_intent(user_prompt):
    lower = _normalize_prompt_text(user_prompt)
    intents = []
    if any(token in lower for token in ("esc", "pause", "keyboard", "input", "event", "keydown", "keyup")):
        intents.append("event loop / keyboard handling")
    if any(token in lower for token in ("restart", "game over", "score", "shoot", "enemy", "movement", "update", "draw")):
        intents.append("gameplay loop")
    if any(token in lower for token in ("ui", "hud", "overlay", "menu")):
        intents.append("ui/hud behavior")
    if not intents:
        intents.append("targeted behavior fix")
    return ", ".join(dict.fromkeys(intents))


def _candidate_snippet(path, project_root, line_no):
    return read_file_snippet(
        path,
        project_root=project_root,
        max_lines=max(18, config.PATCH_SNIPPET_LINES // 3),
        around_line=line_no,
    )


def _hotspot_candidates_text(candidates):
    lines = []
    for idx, item in enumerate(candidates[:3], 1):
        lines.append(f"- #{idx} {item.get('label', '(unknown)')} score={item.get('score', 0)} reason={item.get('reason', 'n/a')}")
    return "\n".join(lines) if lines else "(none)"


def _candidate_by_index(candidates, index):
    if not isinstance(candidates, list) or not candidates:
        return {}
    if index < 0 or index >= len(candidates):
        return {}
    item = candidates[index]
    return item if isinstance(item, dict) else {}


def _action_mentions_hotspot_candidate(action, candidate):
    if not isinstance(candidate, dict):
        return False
    action_type = str(action.get("type", ""))
    if action_type not in {"replace_in_file", "insert_before", "insert_after", "replace_block", "patch_lines", "write_file"}:
        return False
    args = action.get("args") if isinstance(action.get("args"), dict) else {}
    probe = " ".join(
        str(args.get(key, ""))
        for key in ("old", "new", "anchor", "start_anchor", "end_anchor", "content", "new_content")
    )
    probe_norm = _normalize_prompt_text(probe)
    snippet_norm = _normalize_prompt_text(candidate.get("snippet", ""))
    label_norm = _normalize_prompt_text(candidate.get("label", ""))
    if not probe_norm:
        return False
    if label_norm and label_norm in probe_norm:
        return True
    shared_tokens = [token for token in _keyword_tokens(candidate.get("label", "")) if token in probe_norm]
    if shared_tokens:
        return True
    snippet_tokens = [token for token in _keyword_tokens(candidate.get("snippet", "")) if len(token) >= 4]
    overlap = sum(1 for token in snippet_tokens[:20] if token in probe_norm)
    return overlap >= 2 and bool(snippet_norm)


def _action_outside_grounded_hotspots(action, state):
    if state.mode != "patch":
        return False
    action_type = str(action.get("type", ""))
    if action_type not in {"insert_before", "insert_after", "replace_block", "replace_in_file"}:
        return False
    candidates = state.patch_hotspot_candidates if isinstance(state.patch_hotspot_candidates, list) else []
    if not candidates:
        return False
    primary = _candidate_by_index(candidates, state.patch_hotspot_primary_index)
    secondary = _candidate_by_index(candidates, state.patch_hotspot_secondary_index)
    if _action_mentions_hotspot_candidate(action, primary):
        return False
    if _action_mentions_hotspot_candidate(action, secondary):
        return False
    return True


def _negotiate_hotspot_from_actions(state, actions):
    candidates = state.patch_hotspot_candidates if isinstance(state.patch_hotspot_candidates, list) else []
    if len(candidates) < 2:
        return False, ""
    primary = _candidate_by_index(candidates, state.patch_hotspot_primary_index)
    primary_idx = state.patch_hotspot_primary_index
    for idx, candidate in enumerate(candidates[:3]):
        if idx == primary_idx:
            continue
        for action in actions:
            if _action_mentions_hotspot_candidate(action, candidate):
                state.patch_hotspot_primary_index = idx
                state.patch_hotspot_secondary_index = 0 if idx != 0 else (1 if len(candidates) > 1 else 0)
                state.patch_hotspot_label = str(candidate.get("label", ""))
                state.patch_exact_snippet = str(candidate.get("snippet", ""))
                return True, f"agent selected candidate #{idx + 1}"
    return False, ""


def _promote_hotspot_candidate(state, reason):
    candidates = state.patch_hotspot_candidates if isinstance(state.patch_hotspot_candidates, list) else []
    if len(candidates) < 2:
        return False, ""
    current_idx = max(0, int(state.patch_hotspot_primary_index if state.patch_hotspot_primary_index is not None else 0))
    current = _candidate_by_index(candidates, current_idx)
    current_label = str(current.get("label", "")) if current else ""
    if current_label:
        state.patch_hotspot_fail_counts[current_label] = int(state.patch_hotspot_fail_counts.get(current_label, 0)) + 1
    next_idx = max(0, int(state.patch_hotspot_secondary_index if state.patch_hotspot_secondary_index is not None else 1))
    if next_idx == current_idx or next_idx >= len(candidates):
        next_idx = (current_idx + 1) % len(candidates)
    promoted = _candidate_by_index(candidates, next_idx)
    if not promoted:
        return False, ""
    state.patch_hotspot_primary_index = next_idx
    alt = [i for i in range(min(3, len(candidates))) if i != next_idx]
    state.patch_hotspot_secondary_index = alt[0] if alt else next_idx
    state.patch_hotspot_label = str(promoted.get("label", ""))
    state.patch_exact_snippet = str(promoted.get("snippet", ""))
    state.patch_hotspot_last_fail_reason = str(reason or "")
    return True, f"primary -> secondary (#{current_idx + 1} -> #{next_idx + 1})"


def _derive_patch_hotspots(path, project_root, user_prompt, state, history=None, max_items=6):
    outline_items = _discover_file_outline(path, project_root, max_items=18)
    if not outline_items:
        return {
            "summary": "(hotspots unavailable)",
            "snippet": "(snippet unavailable)",
            "anchors": [],
            "selected_hotspot": "",
            "selected_score": 0,
            "focus_context": "",
        }

    tokens = _patch_focus_tokens(user_prompt, state, history=history)
    behavior = _behavior_patch_signals(user_prompt)
    lower_prompt = _normalize_prompt_text(user_prompt)
    pause_input_intent = any(token in lower_prompt for token in ("esc", "pause", "keyboard", "input", "event", "keydown", "keyup"))
    prior_hotspot = _normalize_prompt_text(getattr(state, "patch_hotspot_label", ""))
    scored = []
    task_intent = _derive_patch_task_intent(user_prompt)
    for line_no, text in outline_items:
        lowered = _normalize_prompt_text(text)
        token_hits = sum(1 for token in tokens if token in lowered)
        score = token_hits * 4

        if behavior["active"]:
            if any(marker in lowered for marker in ("def main", " main(", "event", "input", "key", "pause", "update", "draw", "loop", "restart", "score", "enemy", "shoot")):
                score += 8
            if _is_helper_like_hotspot(text):
                score -= 6
        if pause_input_intent:
            if any(marker in lowered for marker in ("def main", " main(", "event", "input", "key", "pause", "loop")):
                score += 6
            if any(marker in lowered for marker in ("draw", "hud", "score")) and "main" not in lowered:
                score -= 3

        if prior_hotspot and (prior_hotspot in lowered or lowered in prior_hotspot):
            score += 2

        fail_penalty = int(state.patch_hotspot_fail_counts.get(text, 0)) * 4
        score -= fail_penalty
        reason_parts = []
        if token_hits:
            reason_parts.append(f"token_hits={token_hits}")
        if behavior["active"] and any(marker in lowered for marker in ("def main", " main(", "event", "input", "key", "pause", "update", "draw", "loop", "restart", "score", "enemy", "shoot")):
            reason_parts.append("behavior-match")
        if _is_helper_like_hotspot(text):
            reason_parts.append("helper-downweight")
        if fail_penalty:
            reason_parts.append(f"fail_penalty={fail_penalty}")
        scored.append((score, line_no, text, ", ".join(reason_parts) or "baseline"))

    scored.sort(key=lambda item: (-item[0], item[1]))
    hotspots = [(line_no, text) for score, line_no, text, _ in scored[:max_items]]
    if not hotspots:
        hotspots = outline_items[:max_items]

    selected_score, selected_line, selected_text, _ = scored[0]
    hotspot_lines = [f"- L{line_no}: {text} (score={score})" for score, line_no, text, _ in scored[:max_items]]
    snippet = read_file_snippet(
        path,
        project_root=project_root,
        max_lines=max(24, config.PATCH_SNIPPET_LINES // 2),
        around_line=selected_line,
    )
    anchors = [text for _, text in hotspots[:8]]
    candidates = []
    for score, line_no, text, reason in scored[:3]:
        candidates.append(
            {
                "label": text,
                "line": line_no,
                "score": int(score),
                "reason": reason,
                "snippet": _candidate_snippet(path, project_root, line_no),
            }
        )
    return {
        "summary": "\n".join(hotspot_lines),
        "snippet": snippet,
        "anchors": anchors,
        "selected_hotspot": selected_text,
        "selected_score": selected_score,
        "focus_context": ", ".join(behavior["buckets"]) if behavior["buckets"] else "",
        "task_intent": task_intent,
        "candidates": candidates,
    }


def build_instruction_bits(
    user_prompt,
    *,
    mode="create",
    project_root="",
    target_files=None,
    active_patch_target="",
    file_outline="",
    real_anchors="",
    file_snippet="",
    exact_patch_snippet="",
    last_error="",
    history=None,
    compact_level=0,
    create_strategy="write_file",
    create_phase="initial_create",
    chunk_session_open=False,
    chunk_target_path="",
    chunk_expected_parts=0,
    chunk_received_parts=0,
    chunk_missing_parts=None,
    chunk_protocol_violation_streak=0,
    patch_phase="patch_target",
    patch_strategy="surgical_patch",
    patch_hotspots_summary="",
    patch_task_intent="",
    hotspot_candidates_text="",
    hotspot_primary_label="",
    hotspot_secondary_label="",
    stale_error_detected=False,
    stale_error_reason="",
    task_profile="",
    model_route="",
    task_shape="project_generation_task",
    source_readonly_files=None,
    derived_allowed_files=None,
    transform_primary_source="",
    large_file_mode="disabled",
    transform_source_summary="",
    transform_source_snippet="",
):
    target_files = target_files or []
    history = history or []
    chunk_missing_parts = list(chunk_missing_parts or [])
    source_readonly_files = list(source_readonly_files or [])
    derived_allowed_files = list(derived_allowed_files or [])
    compact_history = _compact_history(history, compact_level)
    required_b64 = _required_b64_fields(user_prompt)

    if mode == "patch":
        patch_strategy = _normalize_patch_strategy(patch_strategy)
        if patch_strategy == "chunked_rewrite_existing_file":
            strategy_rules = [
                "- Strategy contract: this task has escalated to chunked full rewrite of the existing active patch target.",
                "- Return begin_file_rewrite, then append_file_chunk parts in order, then finalize_file_rewrite for the SAME active patch target.",
                "- Do not use one-shot write_file for this strategy.",
                "- Do not mix chunked rewrite with anchored patch actions in the same reply.",
            ]
        elif patch_strategy == "rewrite_existing_file":
            strategy_rules = [
                "- Strategy contract: this task has escalated to controlled full rewrite of the existing active patch target.",
                "- Prefer one coherent corrected file for the active target, not many tiny scattered edits.",
                "- Keep the rewrite grounded in provided outline/snippets and preserve required behavior.",
                "- Use chunked rewrite if the output becomes large or escaping becomes fragile.",
            ]
        else:
            strategy_rules = [
                "- Strategy contract: surgical patching is active.",
                "- For single-file patch tasks, use this order: replace_in_file, insert_before, insert_after, replace_block, write_file fallback only if necessary.",
                "- Prefer 1-3 small anchored edits for weaker local models.",
                "- Use only one strategy family per iteration. Do not mix anchored edits with rewrite strategy unless absolutely necessary.",
            ]

        return [
            "You are a coding agent working inside an existing local project.",
            f"Project root: {project_root}",
            "Mode: patch",
            f"PATCH PHASE: {patch_phase}",
            f"PATCH STRATEGY: {patch_strategy}",
            f"PATCH TASK INTENT: {patch_task_intent or 'targeted behavior fix'}",
            f"TASK PROFILE: {task_profile or 'standard_patch'}",
            f"MODEL ROUTE: {model_route or 'local/default'}",
            f"TASK SHAPE: {task_shape or 'single_file_patch'}",
            f"User patch request: {user_prompt}",
            f"canonical active_patch_target: {active_patch_target or 'app.py'}",
            f"target_files: {json.dumps(target_files or [active_patch_target or 'app.py'])}",
            "expected_file_count: 1",
            "single_file_task: true",
            "",
            "Return ONLY compact JSON:",
            '{"plan":"short plan","reasoning_short":"short reason","actions":[{"type":"replace_in_file","args":{"path":"app.py","old":"x","new":"y"}}]}',
            "",
            "Allowed actions: replace_in_file, insert_before, insert_after, replace_block, begin_file_rewrite, append_file_chunk, finalize_file_rewrite, write_file, patch_lines, find_in_file, run_cmd",
            "Rules:",
            "- Default to one existing file.",
            "- Keep target fixed: all file actions must stay on active_patch_target unless user explicitly expands scope.",
            "- Source-of-truth priority: current real file snapshot and anchors override older traceback/log text.",
            "- AGENT SHOULD CHOOSE PRIMARY HOTSPOT FROM THESE CANDIDATES and stay within grounded candidate regions.",
            "- Use ONLY anchors that appear in the provided real outline, real anchors, or focused snippets.",
            "- Prefer exact anchor strings copied from the provided file data. Do not invent function or method names.",
            "- Patch/rewrite only mapped hotspot areas unless a clear error proves another area is required.",
            "- Do not use line_number, start_line, or end_line unless absolutely necessary.",
            "- Use patch_lines only as a legacy fallback.",
            "- Use write_file only for small full-file rewrites.",
            "- If full rewrite is needed for a large file, use chunked rewrite protocol.",
            "- For medium/large code payloads with quotes/newlines, prefer safe transport fields: content_b64 / old_b64 / new_b64.",
            "- Keep small/simple edits as plain text when safe; use b64 for bigger blocks to avoid JSON escaping failures.",
            "- For small focused patches, prefer replace_block/insert_before/insert_after using real anchors over large replace_in_file blocks.",
            "- If using replace_in_file, keep old/new compact and exactly grounded in the provided exact snippet.",
            "- For Python replace_block on methods/classes, return a complete valid block body with correct indentation.",
            "- Do not return truncated or partial method fragments.",
            "- Prefer fewer, self-contained method/class replacements over many tiny risky edits.",
            "- If replacing a Python method, include the full method implementation for that method in one coherent block.",
            "- Do not create folders or new files unless the user clearly asked for that.",
            "- Do not overwrite the whole file with a tiny fragment.",
            *strategy_rules,
            "Examples:",
            '{"type":"insert_after","args":{"path":"app.py","anchor":"def main():\\n","content":"    print(\\"ready\\")\\n"}}',
            '{"type":"insert_before","args":{"path":"app.py","anchor":"if __name__ == \\"__main__\\":\\n","content":"\\n# launcher\\n"}}',
            '{"type":"replace_block","args":{"path":"app.py","start_anchor":"    old = 1\\n","end_anchor":"    print(old)\\n","content":"    old = 2\\n    bonus = 3\\n"}}',
            "",
            "Real file outline:",
            file_outline or "(outline unavailable)",
            "",
            "Patch hotspots:",
            patch_hotspots_summary or "(hotspots unavailable)",
            "",
            "Hotspot candidates:",
            hotspot_candidates_text or "(none)",
            f"CURRENT PRIMARY HOTSPOT: {hotspot_primary_label or '(unset)'}",
            f"SECONDARY HOTSPOT: {hotspot_secondary_label or '(unset)'}",
            "If current hotspot fails, use the next grounded candidate instead of inventing a new region.",
            "",
            "Patch context priority:",
            "current snapshot > stale logs",
            f"stale context signal: {stale_error_reason}" if stale_error_detected and stale_error_reason else "stale context signal: none",
            "",
            "Exact grounded snippet for current hotspot:",
            exact_patch_snippet or "(exact snippet unavailable)",
            "",
            "Real anchors from file:",
            real_anchors or "(anchors unavailable)",
            "",
            "Target file snippet:",
            file_snippet or "(snippet unavailable)",
            "",
            "Last error / traceback:",
            last_error or "None",
            "",
            "Recent observations:",
            compact_history or "None",
        ]

    next_required = ""
    if create_strategy == "chunked_rewrite":
        if create_phase == "chunk_begin":
            next_required = "begin_file_rewrite"
        elif create_phase == "chunk_finalize":
            next_required = "finalize_file_rewrite"
        elif create_phase == "chunk_append":
            next_required = "finalize_file_rewrite" if int(chunk_received_parts or 0) >= int(chunk_expected_parts or 0) and int(chunk_expected_parts or 0) > 0 else "append_file_chunk"

    base = [
        "You are a coding agent.",
        f"Project root: {project_root}",
        "Mode: create",
        f"TASK PROFILE: {task_profile or 'standard_create'}",
        f"MODEL ROUTE: {model_route or 'local/default'}",
        f"TASK SHAPE: {task_shape or 'project_generation_task'}",
        f"User task: {user_prompt}",
        "",
        "Return ONLY JSON with this exact shape:",
        '{"plan":"short plan","reasoning_short":"short reason","actions":[{"type":"write_file","args":{"path":"app.py","content":"print(\\"Hello World\\")"}}]}',
        "",
            "Allowed actions: read_file, write_file, replace_in_file, insert_before, insert_after, replace_block, begin_file_rewrite, append_file_chunk, finalize_file_rewrite, patch_lines, find_in_file, run_cmd, mkdir",
            "Rules:",
            "- All file paths must stay inside the project root.",
            "- Prefer writing project files inside this run's folder.",
            "- For Python on Windows, use python, not python3.",
            '- For Windows launcher files (.bat/.cmd), use real newlines and prefer: "@echo off", `cd /d "%~dp0"`, `py -3 app.py` with fallback to `python app.py`, and `pause`.',
            "- If a previous attempt failed, fix it instead of repeating the same broken action.",
            "- If task is complete, return plan=done with empty actions.",
            '- If user explicitly requires content_b64/old_b64/new_b64, use those fields exactly.',
            "- Do not place raw base64 payload in plain content/old/new fields.",
            f"- CREATE STRATEGY: {create_strategy}",
            f"- CREATE PHASE: {create_phase}",
            f"- CHUNK SESSION OPEN: {'true' if chunk_session_open else 'false'}",
            f"- CHUNK TARGET: {chunk_target_path or 'app.py'}",
            f"- CHUNK SESSION STATUS: received {int(chunk_received_parts or 0)}/{int(chunk_expected_parts or 0)} parts",
            f"- CHUNK NEXT REQUIRED ACTION: {next_required or 'none'}",
            "- In verify_or_run phase, prefer run_cmd, verify, and targeted edits over full-file rewrite.",
            "- In fix_existing_file phase, prefer replace_in_file/insert_before/insert_after/replace_block on existing files.",
            f"- LARGE FILE MODE: {large_file_mode}",
            "",
            "Recent observations:",
            compact_history or "None",
    ]
    if task_shape in TRANSFORM_TASK_SHAPES:
        readonly_preview = ", ".join(source_readonly_files[:8]) if source_readonly_files else "(none)"
        derived_preview = ", ".join(derived_allowed_files[:12]) if derived_allowed_files else "(model may propose safe derived outputs)"
        base.extend(
            [
                "",
                "TRANSFORM COPY CONTRACT:",
                "- This is a source-to-derived artifact task, not in-place patching.",
                f"- SOURCE FILE POLICY: read-only -> {readonly_preview}",
                f"- DERIVED FILES ALLOWED: {derived_preview}",
                f"- PRIMARY SOURCE: {transform_primary_source or '(auto)'}",
                "- Do not modify source files unless the user explicitly requests overwrite/in-place edit.",
                "- Prefer deterministic flow: inspect structure -> extract content blocks -> build placeholder map -> write derived outputs.",
                "- For large files, use tool-first incremental analysis and bounded snippets; avoid dumping full raw source each iteration.",
                "- If read_file already returned large-file transport, reuse cached section index/handles instead of repeating identical full-file reads.",
                "- For repeated read_file on same large source, switch to targeted extraction using section ids + find_in_file queries.",
                "- Targeted read patterns: read_file(section_id=\"S4\") OR read_file(line_start=840,line_end=980) OR read_file(around_anchor=\"[fusion_text\")",
                "EXECUTION FRAME:",
                f"- INPUTS: {readonly_preview}",
                f"- OUTPUTS: {derived_preview}",
                f"- OBJECTIVE: {truncate_middle(user_prompt, 260)}",
                "- CONSTRAINTS: keep boilerplate/mockup syntax stable; replace only user-content blocks when requested.",
            ]
        )
        if transform_source_summary:
            base.extend(["- SOURCE SUMMARY: " + transform_source_summary])
        if large_file_mode in {"enabled", "chunk", "strict_chunk"}:
            base.extend(
                [
                    "- LARGE FILE STRATEGY ACTIVE: Step 1 inspect structure only, Step 2 extract bounded blocks, Step 3 build placeholder map, Step 4 assemble outputs, Step 5 verify.",
                ]
            )
        if large_file_mode in {"chunk", "strict_chunk"}:
            base.extend(
                [
                    "- CHUNK ANALYSIS MODE: analyze source in bounded ranges and preserve shortcodes/boilerplate syntax.",
                ]
            )
        if transform_source_snippet:
            base.extend(["", "SOURCE SNIPPET (bounded):", transform_source_snippet])
    if create_strategy == "chunked_rewrite" and chunk_missing_parts:
        preview = ",".join(str(x) for x in chunk_missing_parts[:24])
        if len(chunk_missing_parts) > 24:
            preview += ",..."
        base.extend([
            "",
            f"CHUNK MISSING PARTS: {preview}",
        ])
    if create_strategy == "chunked_rewrite" and int(chunk_protocol_violation_streak or 0) > 0:
        base.extend([
            "",
            "CHUNK PROTOCOL GUIDANCE STRENGTHENED:",
            "- Previous response repeated protocol incorrectly.",
            "- Continue from current phase and required action; do not restart chunk protocol.",
        ])
    return base + (([
            "",
            f"STRICT FORMAT CONTRACT: required fields -> {', '.join(required_b64)}",
            "If required field is missing, return a format-correct action instead of plain content fallback.",
        ] if required_b64 else []) + ([
            "",
            "CHUNKED CREATE CONTRACT:",
            "- Use begin_file_rewrite(path, expected_parts), then append_file_chunk(path, part, content/content_b64), then finalize_file_rewrite(path).",
            "- Do not use one-shot write_file for large main file generation in this strategy.",
            "- Optional run_cmd is allowed only after finalize_file_rewrite.",
            "- If CHUNK SESSION OPEN is true, DO NOT call begin_file_rewrite again.",
            "- Continue append_file_chunk for missing parts, then call finalize_file_rewrite.",
            "- In chunk_append phase, begin_file_rewrite is NOT allowed.",
            '- chunk_append example: {"type":"append_file_chunk","args":{"path":"app.py","part":1,"content":"..."}}',
            "- finalize_file_rewrite is allowed only when all required parts are present.",
        ] if create_strategy == "chunked_rewrite" else []))


def _format_violation_action(message):
    return {"type": "action_format_violation", "args": {"message": message}}


def _is_small_focused_patch(state):
    if state.mode != "patch":
        return False
    if _normalize_patch_strategy(state.patch_strategy) != "surgical_patch":
        return False
    return state.task_profile in {"simple_patch", "standard_patch"}


def _replace_in_file_looks_ungrounded(action, state):
    if not _is_small_focused_patch(state):
        return False, ""
    if action.get("type") != "replace_in_file":
        return False, ""
    args = action.get("args") if isinstance(action.get("args"), dict) else {}
    old_text = str(args.get("old", ""))
    if len(old_text) < 220:
        return False, ""
    snippet = str(state.patch_exact_snippet or "")
    if not snippet:
        return False, ""
    if old_text in snippet:
        return False, ""
    return True, "PATCH ACTION REJECTED: oversized ungrounded replace_in_file old block"


def build_prompt(user_prompt, history, state):
    snippet_lines, _, _ = _prompt_budget(state.prompt_compaction_level)
    file_snippet = ""
    exact_patch_snippet = ""
    file_outline = ""
    real_anchors = ""
    patch_hotspots_summary = ""
    patch_task_intent = str(getattr(state, "patch_task_intent", "") or "")
    hotspot_candidates_text = _hotspot_candidates_text(getattr(state, "patch_hotspot_candidates", []) or [])
    hotspot_primary_label = str(getattr(state, "patch_hotspot_label", "") or "")
    hotspot_secondary_label = ""
    transform_source_summary = ""
    transform_source_snippet = ""
    secondary_item = _candidate_by_index(
        getattr(state, "patch_hotspot_candidates", []) or [],
        int(getattr(state, "patch_hotspot_secondary_index", 1) if getattr(state, "patch_hotspot_secondary_index", 1) is not None else 1),
    )
    if secondary_item:
        hotspot_secondary_label = str(secondary_item.get("label", ""))
    if state.mode == "patch" and state.active_patch_target:
        hotspot_bundle = _derive_patch_hotspots(
            state.active_patch_target,
            state.active_project_root,
            user_prompt,
            state,
            history=history,
        )
        outline_items = _discover_file_outline(state.active_patch_target, state.active_project_root)
        anchor_items = hotspot_bundle.get("anchors") or _discover_real_anchors(state.active_patch_target, state.active_project_root)
        file_outline = "\n".join(f"- L{line_no}: {text}" for line_no, text in outline_items[:18])
        real_anchors = "\n".join(f'- "{text}"' for text in (anchor_items[:12] if isinstance(anchor_items, list) else []))
        patch_hotspots_summary = hotspot_bundle.get("summary", "")
        patch_task_intent = hotspot_bundle.get("task_intent", patch_task_intent)
        derived_candidates = hotspot_bundle.get("candidates") or []
        if not hotspot_candidates_text and derived_candidates:
            hotspot_candidates_text = _hotspot_candidates_text(derived_candidates)
        if not hotspot_primary_label:
            hotspot_primary_label = str(hotspot_bundle.get("selected_hotspot") or "")
        file_snippet = hotspot_bundle.get("snippet") or _focused_patch_snippets(
            state.active_patch_target,
            state.active_project_root,
            user_prompt,
            snippet_lines,
        )
        exact_patch_snippet = hotspot_bundle.get("snippet") or ""
    elif state.mode == "create" and state.task_shape in TRANSFORM_TASK_SHAPES:
        transform_source_summary, transform_source_snippet = _transform_source_context(state)

    bits = build_instruction_bits(
        user_prompt,
        mode=state.mode,
        project_root=state.active_project_root,
        target_files=state.target_files,
        active_patch_target=state.active_patch_target,
        file_outline=file_outline,
        real_anchors=real_anchors,
        file_snippet=file_snippet,
        exact_patch_snippet=exact_patch_snippet,
        last_error=truncate_middle(state.last_runtime_error, 1800),
        history=history,
        compact_level=state.prompt_compaction_level,
        create_strategy=state.create_strategy,
        create_phase=state.create_phase,
        chunk_session_open=state.chunk_session_open,
        chunk_target_path=state.chunk_target_path,
        chunk_expected_parts=state.chunk_expected_parts,
        chunk_received_parts=state.chunk_received_parts,
        chunk_missing_parts=state.chunk_missing_parts,
        chunk_protocol_violation_streak=state.chunk_protocol_violation_streak,
        patch_phase=state.patch_phase,
        patch_strategy=state.patch_strategy,
        patch_hotspots_summary=patch_hotspots_summary,
        patch_task_intent=patch_task_intent,
        hotspot_candidates_text=hotspot_candidates_text,
        hotspot_primary_label=hotspot_primary_label,
        hotspot_secondary_label=hotspot_secondary_label,
        stale_error_detected=bool(getattr(state, "patch_stale_error_detected", False)),
        stale_error_reason=str(getattr(state, "patch_stale_error_reason", "") or ""),
        task_profile=state.task_profile,
        model_route=state.model_route,
        task_shape=state.task_shape,
        source_readonly_files=state.source_readonly_files,
        derived_allowed_files=state.derived_allowed_files,
        transform_primary_source=state.transform_primary_source,
        large_file_mode=state.large_file_mode,
        transform_source_summary=transform_source_summary,
        transform_source_snippet=transform_source_snippet,
    )
    prompt = "\n".join(bit for bit in bits if bit is not None)

    limit = max(1500, int(config.PROMPT_CHAR_LIMIT or 12000))
    if len(prompt) <= limit:
        return prompt

    state.prompt_compaction_level += 1
    snippet_lines, _, _ = _prompt_budget(state.prompt_compaction_level)
    if state.mode == "patch" and state.active_patch_target:
        hotspot_bundle = _derive_patch_hotspots(
            state.active_patch_target,
            state.active_project_root,
            user_prompt,
            state,
            history=history,
            max_items=4,
        )
        outline_items = _discover_file_outline(state.active_patch_target, state.active_project_root, max_items=12)
        anchor_items = hotspot_bundle.get("anchors") or _discover_real_anchors(state.active_patch_target, state.active_project_root, max_items=8)
        file_outline = "\n".join(f"- L{line_no}: {text}" for line_no, text in outline_items[:12])
        real_anchors = "\n".join(f'- "{text}"' for text in (anchor_items[:8] if isinstance(anchor_items, list) else []))
        patch_hotspots_summary = hotspot_bundle.get("summary", "")
        patch_task_intent = hotspot_bundle.get("task_intent", patch_task_intent)
        derived_candidates = hotspot_bundle.get("candidates") or []
        if not hotspot_candidates_text and derived_candidates:
            hotspot_candidates_text = _hotspot_candidates_text(derived_candidates)
        if not hotspot_primary_label:
            hotspot_primary_label = str(hotspot_bundle.get("selected_hotspot") or "")
        file_snippet = hotspot_bundle.get("snippet") or _focused_patch_snippets(
            state.active_patch_target,
            state.active_project_root,
            user_prompt,
            snippet_lines,
        )
        exact_patch_snippet = hotspot_bundle.get("snippet") or exact_patch_snippet
    bits = build_instruction_bits(
        user_prompt,
        mode=state.mode,
        project_root=state.active_project_root,
        target_files=state.target_files,
        active_patch_target=state.active_patch_target,
        file_outline=truncate_middle(file_outline, limit // 5),
        real_anchors=truncate_middle(real_anchors, limit // 6),
        file_snippet=truncate_middle(file_snippet, limit // 2),
        exact_patch_snippet=truncate_middle(exact_patch_snippet, limit // 3),
        last_error=truncate_middle(state.last_runtime_error, limit // 5),
        history=history[-2:],
        compact_level=state.prompt_compaction_level,
        create_strategy=state.create_strategy,
        create_phase=state.create_phase,
        chunk_session_open=state.chunk_session_open,
        chunk_target_path=state.chunk_target_path,
        chunk_expected_parts=state.chunk_expected_parts,
        chunk_received_parts=state.chunk_received_parts,
        chunk_missing_parts=state.chunk_missing_parts,
        chunk_protocol_violation_streak=state.chunk_protocol_violation_streak,
        patch_phase=state.patch_phase,
        patch_strategy=state.patch_strategy,
        patch_hotspots_summary=truncate_middle(patch_hotspots_summary, limit // 6),
        patch_task_intent=truncate_middle(patch_task_intent, limit // 10),
        hotspot_candidates_text=truncate_middle(hotspot_candidates_text, limit // 6),
        hotspot_primary_label=hotspot_primary_label,
        hotspot_secondary_label=hotspot_secondary_label,
        task_profile=state.task_profile,
        model_route=state.model_route,
        task_shape=state.task_shape,
        source_readonly_files=state.source_readonly_files,
        derived_allowed_files=state.derived_allowed_files,
        transform_primary_source=state.transform_primary_source,
        large_file_mode=state.large_file_mode,
        transform_source_summary=truncate_middle(transform_source_summary, limit // 8),
        transform_source_snippet=truncate_middle(transform_source_snippet, limit // 3),
    )
    prompt = "\n".join(bit for bit in bits if bit is not None)
    return truncate_middle(prompt, limit)


def _overflow_like(message):
    lower = (message or "").lower()
    markers = ("n_ctx", "n_keep", "context", "kv cache", "prompt too long", "maximum context")
    return any(marker in lower for marker in markers)


def _fingerprint_plan(plan, actions):
    payload = {
        "plan": plan,
        "actions": actions,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _reset_stuck_state(state):
    state.duplicate_action_cache.clear()
    state.last_plan_fingerprint = ""
    state.stuck_iterations = 0


def _rewrite_state_path_for(rel_path, project_root):
    rel = normalize_rel_path(rel_path or "")
    if not rel:
        return ""
    abs_path = os.path.abspath(os.path.join(project_root, rel))
    key = hashlib.sha256(os.path.normcase(abs_path).encode("utf-8")).hexdigest()
    return os.path.join(project_root, REWRITE_STATE_DIR, f"{key}.json")


def _load_rewrite_state_for(rel_path, project_root):
    state_path = _rewrite_state_path_for(rel_path, project_root)
    if not state_path or not os.path.exists(state_path):
        return None
    try:
        data = read_json_file(state_path)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _clear_rewrite_state_for(rel_path, project_root):
    state_path = _rewrite_state_path_for(rel_path, project_root)
    if not state_path:
        return
    try:
        os.remove(state_path)
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _compute_missing_parts(expected_parts, parts):
    try:
        expected = int(expected_parts or 0)
    except Exception:
        expected = 0
    if expected <= 0:
        return []
    present = set()
    if isinstance(parts, dict):
        for key in parts.keys():
            try:
                idx = int(key)
            except Exception:
                continue
            if 1 <= idx <= expected:
                present.add(idx)
    return [idx for idx in range(1, expected + 1) if idx not in present]


def _chunk_next_required_action(state):
    if state.create_strategy != "chunked_rewrite":
        return ""
    if state.create_phase == "chunk_begin":
        return "begin_file_rewrite"
    if state.create_phase == "chunk_finalize":
        return "finalize_file_rewrite"
    if state.create_phase == "chunk_append":
        if state.chunk_finalize_pending:
            return "finalize_file_rewrite"
        return "append_file_chunk"
    return ""


def _reset_chunk_session(state, reason="", clear_rewrite_state=False):
    if clear_rewrite_state and state.chunk_target_path:
        _clear_rewrite_state_for(state.chunk_target_path, state.active_project_root)
    state.chunk_session_open = False
    state.chunk_target_path = ""
    state.chunk_expected_parts = 0
    state.chunk_received_parts = 0
    state.chunk_missing_parts = []
    state.chunk_finalize_pending = False
    state.chunk_protocol_violation_streak = 0
    if reason:
        state.create_strategy_reason = reason


def _sync_chunk_session_from_rewrite_state(state, log=None, startup=False):
    if state.mode != "create" or state.create_strategy != "chunked_rewrite":
        return

    if not state.chunk_target_path and not state.chunk_session_open and not state.last_created_main_file:
        state.chunk_missing_parts = []
        state.create_phase = "chunk_begin"
        return

    target = normalize_rel_path(state.chunk_target_path or state.last_created_main_file or "app.py")
    state.chunk_target_path = target
    rewrite_data = _load_rewrite_state_for(target, state.active_project_root)

    if not rewrite_data:
        if state.chunk_session_open:
            msg = "CHUNK SESSION RESET: stale state after restart"
            reason = "recorded open session but rewrite_state missing"
            if log:
                log(msg)
                log(f"CHUNK SESSION RESET REASON: {reason}")
            _reset_chunk_session(state, reason=reason, clear_rewrite_state=False)
        state.create_phase = "chunk_begin"
        state.chunk_missing_parts = []
        return

    expected_parts = rewrite_data.get("expected_parts")
    parts = rewrite_data.get("parts")
    if not isinstance(expected_parts, int) or expected_parts < 1 or expected_parts > 200 or not isinstance(parts, dict):
        msg = "CHUNK SESSION RESET: stale state after restart"
        reason = "rewrite state is corrupt or incomplete metadata"
        if log:
            log(msg)
            log(f"CHUNK SESSION RESET REASON: {reason}")
        _reset_chunk_session(state, reason=reason, clear_rewrite_state=True)
        state.create_phase = "chunk_begin"
        return

    missing_parts = _compute_missing_parts(expected_parts, parts)
    received = max(0, expected_parts - len(missing_parts))

    state.chunk_session_open = True
    state.chunk_expected_parts = expected_parts
    state.chunk_received_parts = min(received, expected_parts)
    state.chunk_missing_parts = missing_parts
    state.chunk_finalize_pending = state.chunk_received_parts >= state.chunk_expected_parts

    if startup and state.chunk_expected_parts > 0 and state.chunk_received_parts == 0:
        reason = f"startup open session with 0/{state.chunk_expected_parts} parts"
        if log:
            log("CHUNK SESSION RESET: stale empty session")
            log(f"CHUNK SESSION RESET REASON: {reason}")
        _reset_chunk_session(state, reason=reason, clear_rewrite_state=True)
        state.create_phase = "chunk_begin"
        if log:
            log("CHUNK PHASE: chunk_begin")
        return

    state.create_phase = "chunk_finalize" if state.chunk_finalize_pending else "chunk_append"
    if log:
        phase_label = state.create_phase
        prefix = "CHUNK SESSION: "
        if startup:
            prefix = "CHUNK SESSION (startup): "
        log(
            f"{prefix}open target={state.chunk_target_path} expected_parts={state.chunk_expected_parts}"
        )
        log(f"CHUNK PHASE: {phase_label}")
        log(f"CHUNK SESSION STATUS: received {state.chunk_received_parts}/{state.chunk_expected_parts} parts")
        if state.chunk_missing_parts:
            preview = ",".join(str(i) for i in state.chunk_missing_parts[:12])
            suffix = "..." if len(state.chunk_missing_parts) > 12 else ""
            log(f"CHUNK MISSING PARTS: {preview}{suffix}")


def _build_atomic_patch_stage(actions, state):
    if state.mode != "patch":
        return None

    staged_mutating_actions = {"write_file", "replace_in_file", "insert_before", "insert_after", "replace_block", "patch_lines"}
    counts = {}
    ordered_paths = []
    for action in actions:
        action_type = str(action.get("type", "")).strip()
        if action_type not in staged_mutating_actions:
            continue
        rel_path = str(action.get("args", {}).get("path", "")).strip()
        if not rel_path:
            continue
        counts[rel_path] = counts.get(rel_path, 0) + 1
        if rel_path not in ordered_paths:
            ordered_paths.append(rel_path)

    staged_paths = list(ordered_paths)
    if not staged_paths:
        return None

    base_dir = os.path.join(state.active_project_root, ".agent", "iteration_stage")
    os.makedirs(base_dir, exist_ok=True)
    stage_root = os.path.join(base_dir, f"iter_{state.iteration_count}_{uuid.uuid4().hex[:8]}")
    os.makedirs(stage_root, exist_ok=True)

    for rel_path in staged_paths:
        src = os.path.join(state.active_project_root, rel_path)
        dst = os.path.join(stage_root, rel_path)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(src):
            shutil.copyfile(src, dst)

    return {
        "root": stage_root,
        "paths": staged_paths,
        "path_set": set(staged_paths),
    }


def _cleanup_atomic_patch_stage(stage_context):
    if not stage_context:
        return
    try:
        shutil.rmtree(stage_context.get("root", ""), ignore_errors=True)
    except Exception:
        pass


def _stage_action_project_root(action, state, stage_context):
    if not stage_context or state.mode != "patch":
        return state.active_project_root
    action_type = str(action.get("type", "")).strip()
    rel_path = str(action.get("args", {}).get("path", "")).strip()
    if action_type == "run_cmd":
        return stage_context["root"]
    if action_type in ATOMIC_PATCH_FILE_ACTIONS and rel_path in stage_context.get("path_set", set()):
        return stage_context["root"]
    return state.active_project_root


def _commit_atomic_patch_stage(stage_context, state, log):
    touched_paths = []
    created_files = []
    error_message = ""

    if not stage_context:
        return touched_paths, created_files, error_message

    staged_verify = verify_touched_paths(
        stage_context.get("paths", []),
        project_root=stage_context["root"],
        smoke_run=False,
    )
    if staged_verify.summary:
        log("STAGED " + staged_verify.summary)
    if staged_verify.details:
        log(staged_verify.details)
    if not staged_verify.ok:
        error_message = staged_verify.details or staged_verify.summary
        return touched_paths, created_files, error_message

    for rel_path in stage_context.get("paths", []):
        staged_path = os.path.join(stage_context["root"], rel_path)
        if not os.path.exists(staged_path):
            continue
        with open(staged_path, "r", encoding="utf-8") as handle:
            staged_content = handle.read()
        existed_before = os.path.exists(os.path.join(state.active_project_root, rel_path))
        observation = write_file(
            rel_path,
            staged_content,
            project_root=state.active_project_root,
            patch_mode=True,
            allow_create=False,
        )
        if not observation.ok:
            log(observation.summary)
            if observation.details:
                log(observation.details)
            error_message = observation.details or observation.summary
            break
        if observation.changed:
            log(f"ATOMIC COMMIT {rel_path}")
            touched_paths.append(rel_path)
            if not existed_before:
                created_files.append(rel_path)

    return touched_paths, created_files, error_message


def _hard_rescue_handoff(state, history, log, reason, rescue_decider=None):
    if _rescue_mode() != "ON":
        if _rescue_mode() == "ASK_BEFORE_RESCUE":
            if callable(rescue_decider):
                try:
                    allowed = bool(rescue_decider(reason, state.current_provider, "openai"))
                except Exception:
                    allowed = False
                if not allowed:
                    suppressed = "ASK_BEFORE_RESCUE declined"
                    log(f"RESCUE SUPPRESSED: {suppressed}")
                    history.append(f"RESCUE SUPPRESSED: {suppressed}")
                    return False
            else:
                suppressed = "ASK_BEFORE_RESCUE has no dialog handler"
                log(f"RESCUE SUPPRESSED: {suppressed}")
                history.append(f"RESCUE SUPPRESSED: {suppressed}")
                return False
        else:
            suppressed = _rescue_suppressed_reason()
            log(f"RESCUE SUPPRESSED: {suppressed}")
            history.append(f"RESCUE SUPPRESSED: {suppressed}")
            return False
    if _rescue_mode() != "ON" and _rescue_mode() != "ASK_BEFORE_RESCUE":
        suppressed = _rescue_suppressed_reason()
        log(f"RESCUE SUPPRESSED: {suppressed}")
        history.append(f"RESCUE SUPPRESSED: {suppressed}")
        return False
    if (
        state.current_provider.lower() != "openai"
        and config.OPENAI_RESCUE_ENABLED
        and config.OPENAI_API_KEY.strip()
    ):
        state.current_provider = "openai"
        state.task_profile = "rescue"
        state.model_route = "rescue"
        state.route_reason = str(reason or "")
        state.rescue_handoff_count += 1
        _reset_stuck_state(state)
        state.prompt_compaction_level = max(state.prompt_compaction_level, 1)
        history.append(f"HARD RESCUE HANDOFF: {reason}")
        log(f"HARD RESCUE HANDOFF -> {reason}")
        return True
    return False


def _refresh_patch_context(state):
    if state.mode != "patch":
        return
    current = state.active_patch_target
    if current:
        abs_current = os.path.abspath(os.path.join(state.active_project_root, current))
        if os.path.exists(abs_current):
            state.target_files = [current]
            return
    state.active_patch_target = _prefer_last_sensible_py(state.active_project_root, state.last_written_files)
    state.target_files = [state.active_patch_target]


def _sanitize_action_path(raw_path, state):
    if state.mode == "patch":
        fallback = state.active_patch_target
    elif state.task_shape in TRANSFORM_TASK_SHAPES:
        fallback = (state.derived_allowed_files[0] if state.derived_allowed_files else "")
    else:
        fallback = "app.py"
    clean = _sanitize_target_token(raw_path or fallback, state.active_project_root, patch_mode=state.mode == "patch")
    if clean:
        return clean

    if state.mode == "patch":
        return state.active_patch_target
    if state.task_shape in TRANSFORM_TASK_SHAPES and not str(raw_path or fallback).strip():
        return ""

    candidate = normalize_rel_path(raw_path or fallback)
    abs_candidate = os.path.abspath(os.path.join(state.active_project_root, candidate))
    if is_subpath(state.active_project_root, abs_candidate):
        return candidate
    return fallback


def _off_target_action(raw_path, state):
    raw_text = str(raw_path or "").strip()
    if not raw_text:
        raw_text = "<empty>"
    return {
        "type": "off_target_patch_action",
        "args": {
            "message": (
                "OFF-TARGET PATCH ACTION REJECTED: "
                f"{raw_text} does not match active patch target {state.active_patch_target or 'app.py'}"
            )
        },
    }


def _normalize_action_path_or_reject(raw_path, state):
    if state.mode != "patch" or not state.single_file_task:
        return _sanitize_action_path(raw_path, state), None
    if not state.active_patch_target:
        return _sanitize_action_path(raw_path, state), None

    explicit = str(raw_path or "").strip()
    if not explicit:
        return state.active_patch_target, None

    resolved = _sanitize_target_token(explicit, state.active_project_root, patch_mode=True)
    if not resolved:
        return "", _off_target_action(explicit, state)

    if os.path.normcase(os.path.normpath(resolved)) != os.path.normcase(os.path.normpath(state.active_patch_target)):
        return "", _off_target_action(explicit, state)

    return state.active_patch_target, None


def _normalize_action(action, state):
    action_type = str(action.get("type", "")).strip()
    args = action.get("args") if isinstance(action.get("args"), dict) else {}
    args = dict(args)

    def _first_arg(*keys, default=""):
        for key in keys:
            if key in args and args.get(key) not in (None, ""):
                return args.get(key)
        return default

    if action_type in PATCH_FILE_ACTIONS:
        normalized_path, rejection = _normalize_action_path_or_reject(args.get("path"), state)
        if rejection:
            return rejection
    else:
        normalized_path = ""

    if action_type == "write_file":
        blocked, message = _is_transform_mutation_blocked(action_type, normalized_path, state)
        if blocked:
            return _format_violation_action(message)
        content_value = _first_arg("content", "text", "body", "code", "contents", default="")
        if "content_b64" in state.required_b64_fields:
            if not args.get("content_b64"):
                content_text = str(content_value)
                if _looks_like_base64_text(content_text):
                    return _format_violation_action("ACTION FORMAT VIOLATION: base64 payload was placed in plain content instead of content_b64")
                if content_text:
                    return _format_violation_action("ACTION FORMAT VIOLATION: content_b64 required but plain content was returned")
                return _format_violation_action("ACTION FORMAT VIOLATION: content_b64 required but missing")
        return {
            "type": action_type,
            "args": {
                "path": normalized_path,
                "content": str(content_value),
                "content_b64": args.get("content_b64"),
            },
        }

    if action_type == "read_file":
        line_window = _first_arg("lines", "line_range", default="")
        return {
            "type": action_type,
            "args": {
                "path": _sanitize_action_path(args.get("path"), state),
                "max_chars": args.get("max_chars", args.get("limit")),
                "section_id": _first_arg("section_id", "section", "section_handle", default=""),
                "line_start": args.get("line_start", args.get("start_line", line_window)),
                "line_end": args.get("line_end", args.get("end_line")),
                "around_anchor": _first_arg("around_anchor", "anchor", default=""),
                "query": _first_arg("query", "pattern", "needle", "search", default=""),
            },
        }

    if action_type == "replace_in_file":
        blocked, message = _is_transform_mutation_blocked(action_type, normalized_path, state)
        if blocked:
            return _format_violation_action(message)
        old_value = _first_arg("old", "find", "from_text", "old_content", default="")
        new_value = _first_arg("new", "replace", "to_text", "new_text", "replacement", default="")
        if "old_b64" in state.required_b64_fields and not args.get("old_b64") and str(args.get("old", "")):
            return _format_violation_action("ACTION FORMAT VIOLATION: old_b64 required but plain old was returned")
        if "new_b64" in state.required_b64_fields and not args.get("new_b64"):
            new_text = str(new_value)
            if _looks_like_base64_text(new_text):
                return _format_violation_action("ACTION FORMAT VIOLATION: base64 payload was placed in plain new instead of new_b64")
            if new_text:
                return _format_violation_action("ACTION FORMAT VIOLATION: new_b64 required but plain new was returned")
            return _format_violation_action("ACTION FORMAT VIOLATION: new_b64 required but missing")
        return {
            "type": action_type,
            "args": {
                "path": normalized_path,
                "old": str(old_value),
                "new": str(new_value),
                "old_b64": args.get("old_b64"),
                "new_b64": args.get("new_b64"),
            },
        }

    if action_type == "patch_lines":
        blocked, message = _is_transform_mutation_blocked(action_type, normalized_path, state)
        if blocked:
            return _format_violation_action(message)
        return {
            "type": action_type,
            "args": {
                "path": normalized_path,
                "start_line": args.get("start_line"),
                "end_line": args.get("end_line"),
                "new_content": str(args.get("new_content", args.get("content", ""))),
            },
        }

    if action_type in {"insert_before", "insert_after"}:
        blocked, message = _is_transform_mutation_blocked(action_type, normalized_path, state)
        if blocked:
            return _format_violation_action(message)
        if "content_b64" in state.required_b64_fields and not args.get("content_b64") and not args.get("new_content_b64"):
            content_text = str(args.get("content", args.get("new_content", "")))
            if _looks_like_base64_text(content_text):
                return _format_violation_action("ACTION FORMAT VIOLATION: base64 payload was placed in plain content instead of content_b64")
            if content_text:
                return _format_violation_action("ACTION FORMAT VIOLATION: content_b64 required but plain content was returned")
        fallback_anchor = args.get("target", "")
        if isinstance(fallback_anchor, int):
            fallback_anchor = ""
        return {
            "type": action_type,
            "args": {
                "path": normalized_path,
                "anchor": str(args.get("anchor", fallback_anchor)),
                "content": str(args.get("content", args.get("new_content", ""))),
                "content_b64": args.get("content_b64", args.get("new_content_b64")),
                "line_number": args.get("line_number", args.get("line")),
            },
        }

    if action_type == "replace_block":
        blocked, message = _is_transform_mutation_blocked(action_type, normalized_path, state)
        if blocked:
            return _format_violation_action(message)
        if "content_b64" in state.required_b64_fields and not args.get("content_b64") and not args.get("new_content_b64"):
            content_text = str(args.get("content", args.get("new_content", "")))
            if _looks_like_base64_text(content_text):
                return _format_violation_action("ACTION FORMAT VIOLATION: base64 payload was placed in plain content instead of content_b64")
            if content_text:
                return _format_violation_action("ACTION FORMAT VIOLATION: content_b64 required but plain content was returned")
        start_fallback = args.get("start", "")
        end_fallback = args.get("end", "")
        if isinstance(start_fallback, int):
            start_fallback = ""
        if isinstance(end_fallback, int):
            end_fallback = ""
        return {
            "type": action_type,
            "args": {
                "path": normalized_path,
                "start_anchor": str(args.get("start_anchor", start_fallback)),
                "end_anchor": str(args.get("end_anchor", end_fallback)),
                "content": str(args.get("content", args.get("new_content", ""))),
                "content_b64": args.get("content_b64", args.get("new_content_b64")),
                "start_line": args.get("start_line"),
                "end_line": args.get("end_line"),
            },
        }

    if action_type == "begin_file_rewrite":
        blocked, message = _is_transform_mutation_blocked(action_type, normalized_path, state)
        if blocked:
            return _format_violation_action(message)
        return {
            "type": action_type,
            "args": {
                "path": normalized_path,
                "expected_parts": args.get("expected_parts", args.get("parts")),
            },
        }

    if action_type == "append_file_chunk":
        blocked, message = _is_transform_mutation_blocked(action_type, normalized_path, state)
        if blocked:
            return _format_violation_action(message)
        if "content_b64" in state.required_b64_fields and not args.get("content_b64"):
            content_text = str(args.get("content", args.get("chunk", "")))
            if _looks_like_base64_text(content_text):
                return _format_violation_action("ACTION FORMAT VIOLATION: base64 payload was placed in plain content instead of content_b64")
            if content_text:
                return _format_violation_action("ACTION FORMAT VIOLATION: content_b64 required but plain content was returned")
        return {
            "type": action_type,
            "args": {
                "path": normalized_path,
                "part": args.get("part", args.get("index")),
                "content": str(args.get("content", args.get("chunk", ""))),
                "content_b64": args.get("content_b64"),
            },
        }

    if action_type == "finalize_file_rewrite":
        blocked, message = _is_transform_mutation_blocked(action_type, normalized_path, state)
        if blocked:
            return _format_violation_action(message)
        return {
            "type": action_type,
            "args": {
                "path": normalized_path,
            },
        }

    if action_type == "find_in_file":
        return {
            "type": action_type,
            "args": {
                "path": normalized_path,
                "query": str(_first_arg("query", "pattern", "text", "needle", "search", default="")),
            },
        }

    if action_type == "mkdir":
        return {
            "type": action_type,
            "args": {
                "path": _sanitize_action_path(args.get("path"), state),
            },
        }

    if action_type == "run_cmd":
        return {
            "type": action_type,
            "args": {
                "cmd": normalize_cmd_paths(_first_arg("cmd", "command", default="")),
            },
        }

    return {"type": action_type, "args": args}


def _execute_action(action, state, project_root_override=None, stop_checker=None):
    action_type = action.get("type")
    args = action.get("args", {})
    project_root = project_root_override or state.active_project_root

    if action_type == "action_format_violation":
        from contracts import Observation

        return Observation(
            False,
            "ACTION FORMAT VIOLATION",
            changed=False,
            details=str(args.get("message", "Action format contract failed.")),
            tool=action_type,
        )

    if action_type == "off_target_patch_action":
        from contracts import Observation

        return Observation(
            False,
            "OFF-TARGET PATCH ACTION REJECTED",
            changed=False,
            details=str(args.get("message", "Action path does not match active patch target.")),
            tool=action_type,
        )

    if action_type == "write_file":
        return write_file(
            args.get("path", ""),
            args.get("content", ""),
            project_root=project_root,
            patch_mode=(state.mode == "patch"),
            allow_create=(state.mode != "patch"),
            content_b64=args.get("content_b64"),
        )

    if action_type == "read_file":
        return read_file(
            args.get("path", ""),
            project_root=project_root,
            max_chars=args.get("max_chars"),
            section_id=args.get("section_id"),
            line_start=args.get("line_start"),
            line_end=args.get("line_end"),
            around_anchor=args.get("around_anchor"),
            query=args.get("query"),
        )

    if action_type == "replace_in_file":
        return replace_in_file(
            args.get("path", ""),
            args.get("old", ""),
            args.get("new", ""),
            project_root=project_root,
            old_b64=args.get("old_b64"),
            new_b64=args.get("new_b64"),
        )

    if action_type == "insert_before":
        return insert_before(
            args.get("path", ""),
            args.get("anchor", ""),
            args.get("content", ""),
            project_root=project_root,
            line_number=args.get("line_number"),
            content_b64=args.get("content_b64"),
        )

    if action_type == "insert_after":
        return insert_after(
            args.get("path", ""),
            args.get("anchor", ""),
            args.get("content", ""),
            project_root=project_root,
            line_number=args.get("line_number"),
            content_b64=args.get("content_b64"),
        )

    if action_type == "replace_block":
        return replace_block(
            args.get("path", ""),
            args.get("start_anchor", ""),
            args.get("end_anchor", ""),
            args.get("content", ""),
            project_root=project_root,
            start_line=args.get("start_line"),
            end_line=args.get("end_line"),
            new_content=args.get("new_content"),
            content_b64=args.get("content_b64"),
        )

    if action_type == "patch_lines":
        return patch_lines(
            args.get("path", ""),
            args.get("start_line"),
            args.get("end_line"),
            args.get("new_content", ""),
            project_root=project_root,
        )

    if action_type == "begin_file_rewrite":
        return begin_file_rewrite(
            args.get("path", ""),
            args.get("expected_parts"),
            project_root=project_root,
            patch_mode=(state.mode == "patch"),
            allow_create=(state.mode != "patch"),
        )

    if action_type == "append_file_chunk":
        return append_file_chunk(
            args.get("path", ""),
            args.get("part"),
            args.get("content", ""),
            project_root=project_root,
            content_b64=args.get("content_b64"),
        )

    if action_type == "finalize_file_rewrite":
        return finalize_file_rewrite(
            args.get("path", ""),
            project_root=project_root,
            patch_mode=(state.mode == "patch"),
            allow_create=(state.mode != "patch"),
        )

    if action_type == "find_in_file":
        return find_in_file(
            args.get("path", ""),
            args.get("query", ""),
            project_root=project_root,
        )

    if action_type == "mkdir":
        return mkdir(
            args.get("path", ""),
            project_root=project_root,
            patch_mode=(state.mode == "patch"),
        )

    if action_type == "run_cmd":
        return run_cmd(
            args.get("cmd", ""),
            cwd=project_root,
            project_root=project_root,
            stop_checker=stop_checker,
        )

    from contracts import Observation

    return Observation(False, f"UNKNOWN ACTION: {action_type}", changed=False)


def _format_history_observation(summary, details):
    if details:
        return summary + " | " + truncate_middle(details, 1200)
    return summary


def _compact_parse_error_for_history(message):
    text = str(message or "").strip()
    if not text:
        return "PARSE_ERROR"
    if "PARSE_ERROR" not in text:
        return truncate_middle(text, 240)
    if ": " in text:
        head, tail = text.split(": ", 1)
        return f"{head}: {truncate_middle(tail, 220)}"
    return truncate_middle(text, 240)


def _plan_indicates_completion(plan):
    lower = _normalize_prompt_text(plan)
    return any(keyword in lower for keyword in PATCH_DONE_KEYWORDS)


def _completion_decision(state, plan, had_run_cmd, touched_paths, verify_observation, meaningful_materialization=False):
    verified_ok = not touched_paths or bool(verify_observation.ok)
    plan_done = _plan_indicates_completion(plan)
    patch_target_touched = False
    if state.mode == "patch" and state.active_patch_target:
        patch_target_touched = state.active_patch_target in touched_paths

    if state.mode == "create":
        if had_run_cmd and verified_ok:
            return True, "EXECUTION/CHECK PASSED -> STOP"
        grounded_create_done = bool(touched_paths) or bool(meaningful_materialization) or had_run_cmd
        if plan_done and verified_ok and grounded_create_done:
            return True, "MODEL INDICATED COMPLETION -> STOP"
        return False, "WRITE/VERIFY OK BUT CONTINUE"

    if state.single_file_task and patch_target_touched and verified_ok:
        return True, "SINGLE-FILE PATCH VERIFIED -> STOP"
    if plan_done and patch_target_touched and verified_ok:
        return True, "PATCH COMPLETE -> STOP"
    if had_run_cmd and patch_target_touched and verified_ok:
        return True, "PATCH VERIFIED BY RUN -> STOP"
    return False, "PATCH APPLIED BUT CONTINUE"


def _single_file_patch_retry_note(state):
    stale_hint = ""
    if getattr(state, "patch_stale_error_detected", False):
        stale_hint = " current snapshot overrides stale traceback/log hints."
    return (
        f"SINGLE-FILE PATCH FALLBACK for {state.active_patch_target or 'app.py'}: "
        f"current strategy={_normalize_patch_strategy(getattr(state, 'patch_strategy', 'surgical_patch'))}. "
        "prefer anchored edits (replace_in_file, insert_before/after, replace_block). "
        "If patching keeps failing or file scope is large, escalate to rewrite_existing_file or chunked_rewrite_existing_file. "
        "For chunked rewrite use begin_file_rewrite + append_file_chunk + finalize_file_rewrite."
        f"{stale_hint}"
    )


def run(
    prompt,
    logger=print,
    stop_checker=None,
    model_timeout_handler=None,
    rescue_decider=None,
    max_iterations_handler=None,
):
    state = _build_state(prompt)
    os.makedirs(state.active_project_root, exist_ok=True)
    ensure_gitignore(state.active_project_root)
    if not git_is_repo(state.active_project_root):
        git_init(state.active_project_root)
    _save_session_state(state)

    log_path = os.path.join(os.getcwd(), config.LOG_FILE)
    raw_log_path = os.path.join(os.getcwd(), config.RAW_LOG_FILE)
    os.makedirs(os.path.dirname(raw_log_path), exist_ok=True)

    with open(log_path, "w", encoding="utf-8") as log_file, open(raw_log_path, "w", encoding="utf-8") as raw_log_file:
        tee = Tee(sys.stdout, log_file)

        pending_log_line = None
        pending_log_count = 0

        def flush_pending_log():
            nonlocal pending_log_line, pending_log_count
            if not pending_log_line:
                return
            line = pending_log_line
            if pending_log_count > 1:
                line = f"{line} [same event repeated x{pending_log_count}]"
            tee.write(line + "\n")
            if logger is not None:
                logger(str(line))
            pending_log_line = None
            pending_log_count = 0

        def log(msg=""):
            nonlocal pending_log_line, pending_log_count
            raw_text = str(msg)
            try:
                raw_log_file.write(raw_text + "\n")
                raw_log_file.flush()
            except Exception:
                pass
            compact = _compact_log_message(raw_text)
            if compact == pending_log_line:
                pending_log_count += 1
                return
            flush_pending_log()
            pending_log_line = compact
            pending_log_count = 1

        def stop_requested():
            return bool(stop_checker and stop_checker())

        def request_max_iteration_extension(current_iteration, current_limit):
            if not callable(max_iterations_handler):
                return "kill"
            try:
                return max_iterations_handler(current_iteration, current_limit)
            except Exception:
                return "kill"

        history = []
        consecutive_parse_errors = 0
        empty_done_retry_used = False
        finished_success = False
        terminal_reason = ""
        run_executed_actions = 0
        run_blocked_actions = 0

        log(f"MODE: {state.mode}")
        log(f"MODE CONTROL: {str(getattr(config, 'MODE_CONTROL', 'AUTO') or 'AUTO').strip().upper()}")
        log(f"RESCUE MODE: {_rescue_mode()}")
        log(f"MODE REASON: {state.mode_reason or 'unknown'}")
        log(f"TASK SHAPE: {state.task_shape or 'unknown'}")
        log(f"TASK SHAPE REASON: {state.task_shape_reason or 'n/a'}")
        log(f"PATCH_FILES REPR: {repr(config.PATCH_FILES)}")
        log(f"ACTIVE PROJECT ROOT: {state.active_project_root}")
        if state.task_shape in TRANSFORM_TASK_SHAPES:
            source_preview = ", ".join(state.source_readonly_files[:12]) if state.source_readonly_files else "(none)"
            derived_preview = ", ".join(state.derived_allowed_files[:12]) if state.derived_allowed_files else "(none)"
            log(f"SOURCE FILE POLICY: read-only -> {source_preview}")
            log(f"DERIVED FILES ALLOWED: {derived_preview}")
            log(f"LARGE FILE MODE: {state.large_file_mode}")
            log("PATCH HEURISTICS: disabled")
        else:
            log("PATCH HEURISTICS: enabled" if state.mode == "patch" else "PATCH HEURISTICS: disabled")
        if _rescue_mode() == "OFF":
            log(f"RESCUE SUPPRESSED: {_rescue_suppressed_reason()}")
            log("LOCAL FALLBACK STEP: active (OpenAI rescue disabled)")
        initial_profile, initial_profile_reason = _choose_task_profile(state, prompt, consecutive_parse_errors=0)
        initial_route, initial_provider, initial_route_reason = _route_for_task_profile(state, initial_profile)
        state.task_profile = initial_profile
        state.model_route = initial_route
        state.route_reason = initial_profile_reason or initial_route_reason
        state.current_provider = initial_provider
        log(f"TASK PROFILE: {state.task_profile}")
        log(f"MODEL ROUTE: {state.model_route} ({state.current_provider})")
        if str(state.current_provider or "").lower() == "lmstudio":
            log(f"LOCAL MODEL: {config.LMSTUDIO_MODEL}")
        if state.mode == "patch":
            log(f"ACTIVE PATCH TARGET: {state.active_patch_target}")
            log(f"PATCH PHASE: {state.patch_phase}")
            log(f"PATCH STRATEGY: {state.patch_strategy}")
            if state.patch_strategy_reason:
                log(f"PATCH STRATEGY REASON: {state.patch_strategy_reason}")
        if state.mode == "create":
            if state.create_strategy == "chunked_rewrite":
                _sync_chunk_session_from_rewrite_state(state, log=log, startup=True)
            log(f"CREATE STRATEGY: {state.create_strategy}")
            log(f"CREATE STRATEGY REASON: {state.create_strategy_reason or 'n/a'}")
            log(f"CREATE PHASE: {state.create_phase}")
            if state.create_strategy == "chunked_rewrite":
                log(
                    f"CHUNK SESSION: {'open' if state.chunk_session_open else 'closed'} "
                    f"target={state.chunk_target_path or 'app.py'} expected_parts={state.chunk_expected_parts or 0}"
                )
                log(f"CHUNK SESSION STATUS: received {state.chunk_received_parts or 0}/{state.chunk_expected_parts or 0} parts")
                next_required = _chunk_next_required_action(state)
                if next_required:
                    log(f"CHUNK NEXT REQUIRED ACTION: {next_required}")

        for iteration in range(1000000):
            current_limit = max(1, coerce_int(config.MAX_ITERATIONS, 10, minimum=1, maximum=100000))
            if iteration >= current_limit:
                log(f"MAX ITERATIONS REACHED: {iteration}/{current_limit}")
                decision = request_max_iteration_extension(iteration, current_limit)
                if isinstance(decision, str) and decision.strip().lower() == "kill":
                    terminal_reason = f"MAX ITERATIONS REACHED ({iteration}/{current_limit})"
                    log("MAX ITERATIONS DIALOG -> KILL")
                    break
                add_more = coerce_int(decision, 0, minimum=0, maximum=100)
                if add_more > 0:
                    config.MAX_ITERATIONS = current_limit + add_more
                    log(f"MAX ITERATIONS EXTENDED -> {config.MAX_ITERATIONS}")
                    history.append(f"MAX ITERATIONS EXTENDED by +{add_more}")
                    _save_session_state(state)
                    continue
                terminal_reason = f"MAX ITERATIONS REACHED ({iteration}/{current_limit})"
                log("MAX ITERATIONS DIALOG -> KILL")
                break
            state.iteration_count = iteration + 1
            _save_session_state(state)
            if stop_requested():
                log("STOP REQUESTED")
                break

            previous_profile = state.task_profile
            previous_route = state.model_route
            profile, profile_reason = _choose_task_profile(state, prompt, consecutive_parse_errors=consecutive_parse_errors)
            route, provider_for_iter, route_reason = _route_for_task_profile(state, profile)
            state.task_profile = profile
            state.model_route = route
            state.route_reason = profile_reason or route_reason
            state.current_provider = provider_for_iter
            log(f"TASK PROFILE: {state.task_profile}")
            log(f"MODEL ROUTE: {state.model_route} ({state.current_provider})")
            if str(state.current_provider or "").lower() == "lmstudio":
                log(f"LOCAL MODEL: {config.LMSTUDIO_MODEL}")
            if state.task_profile == "rescue" and state.model_route != "rescue":
                log(f"RESCUE SUPPRESSED: {_rescue_suppressed_reason()}")
            if (previous_profile and previous_profile != state.task_profile) or (previous_route and previous_route != state.model_route):
                log(f"ROUTE ESCALATION REASON: {state.route_reason or 'profile/route change'}")

            if state.mode == "patch":
                _refresh_patch_context(state)
                stale_detected, stale_reason = _detect_stale_error_context(
                    state.active_patch_target,
                    state.active_project_root,
                    prompt,
                    state,
                    history=history,
                )
                state.patch_stale_error_detected = bool(stale_detected)
                state.patch_stale_error_reason = str(stale_reason or "")
                if state.patch_stale_error_detected:
                    log("PATCH CONTEXT PRIORITY: current snapshot > stale logs")
                    log("STALE ERROR CONTEXT DETECTED")
                    if state.patch_stale_error_reason:
                        log(f"STALE ERROR REASON: {state.patch_stale_error_reason}")
                hotspot_bundle = _derive_patch_hotspots(
                    state.active_patch_target,
                    state.active_project_root,
                    prompt,
                    state,
                    history=history,
                )
                hotspot_summary = hotspot_bundle.get("summary", "(hotspots unavailable)")
                selected_hotspot = str(hotspot_bundle.get("selected_hotspot") or "")
                selected_score = hotspot_bundle.get("selected_score", 0)
                focus_context = str(hotspot_bundle.get("focus_context") or "")
                task_intent = str(hotspot_bundle.get("task_intent") or "")
                candidates = hotspot_bundle.get("candidates") or []
                if isinstance(candidates, list) and candidates:
                    state.patch_hotspot_candidates = candidates[:3]
                    preferred_idx = 0
                    for idx, item in enumerate(state.patch_hotspot_candidates):
                        if _normalize_prompt_text(str(item.get("label", ""))) == _normalize_prompt_text(selected_hotspot):
                            preferred_idx = idx
                            break
                    state.patch_hotspot_primary_index = preferred_idx
                    choices = [i for i in range(min(3, len(state.patch_hotspot_candidates))) if i != preferred_idx]
                    state.patch_hotspot_secondary_index = choices[0] if choices else preferred_idx
                state.patch_task_intent = task_intent
                state.patch_hotspot_label = selected_hotspot
                state.patch_exact_snippet = str(hotspot_bundle.get("snippet") or "")
                if (
                    state.last_runtime_error
                    and not state.patch_stale_error_detected
                    and _behavior_patch_signals(prompt).get("active")
                ):
                    runtime_symbols = _extract_error_symbols(state.last_runtime_error)
                    selected_norm = _normalize_prompt_text(state.patch_exact_snippet)
                    if runtime_symbols and not any(symbol in selected_norm for symbol in runtime_symbols[:6]):
                        state.patch_stale_error_detected = True
                        state.patch_stale_error_reason = "traceback symbols do not match selected hotspot snippet"
                        log("STALE TRACEBACK DOWNWEIGHTED")
                hotspots_unavailable = "(hotspots unavailable)" in _normalize_prompt_text(hotspot_summary)
                if hotspots_unavailable:
                    state.patch_hotspot_unavailable_streak += 1
                else:
                    state.patch_hotspot_unavailable_streak = 0
                strategy_escalation_reason = _maybe_escalate_patch_strategy(state)
                log(f"PATCH PHASE: {state.patch_phase}")
                log(f"PATCH STRATEGY: {state.patch_strategy}")
                if state.patch_hotspot_label:
                    log(f"PATCH HOTSPOT SELECTED: {state.patch_hotspot_label}")
                    log(f"PATCH HOTSPOT SCORE: {selected_score}")
                    log(f"PATCH GROUNDING: exact snippet for {state.patch_hotspot_label}")
                    log(f"PATCH GROUNDING SOURCE: current {state.patch_hotspot_label} snippet")
                if state.patch_task_intent:
                    log(f"PATCH TASK INTENT: {state.patch_task_intent}")
                for idx, item in enumerate(state.patch_hotspot_candidates[:3], 1):
                    log(
                        f"HOTSPOT CANDIDATE #{idx}: {item.get('label', '(unknown)')} "
                        f"score={item.get('score', 0)} reason={item.get('reason', 'n/a')}"
                    )
                primary_item = _candidate_by_index(state.patch_hotspot_candidates, state.patch_hotspot_primary_index)
                secondary_item = _candidate_by_index(state.patch_hotspot_candidates, state.patch_hotspot_secondary_index)
                if primary_item:
                    log(f"HOTSPOT PRIMARY SELECTED: {primary_item.get('label', '(unknown)')}")
                if secondary_item:
                    log(f"HOTSPOT SECONDARY SELECTED: {secondary_item.get('label', '(unknown)')}")
                if focus_context:
                    log(f"PATCH CONTEXT FOCUS: {focus_context}")
                if _is_small_focused_patch(state):
                    log("PATCH ACTION BIAS: prefer replace_block")
                if strategy_escalation_reason:
                    log(f"PATCH REWRITE ESCALATION REASON: {strategy_escalation_reason}")
                    history.append(f"PATCH REWRITE ESCALATION REASON: {strategy_escalation_reason}")
                log("PATCH HOTSPOTS: " + truncate_middle(hotspot_summary, 500))
                if state.patch_phase == "inspect_target":
                    history.append("Patch inspection summary:\n" + truncate_middle(hotspot_summary, 1500))
                    history.append("Patch inspection snippet:\n" + truncate_middle(hotspot_bundle.get("snippet", ""), 1600))
                    state.patch_phase = "patch_target"
                    _save_session_state(state)
                    continue
            if state.mode == "create":
                if state.task_shape == "transform_copy_task":
                    log(f"TRANSFORM PHASE: {state.transform_phase}")
                if state.create_strategy == "chunked_rewrite":
                    _sync_chunk_session_from_rewrite_state(state, log=log, startup=False)
                if (
                    state.create_strategy != "chunked_rewrite"
                    and state.create_full_write_streak >= (3 if "chunk protocol failure fallback" in str(state.create_strategy_reason or "") else 2)
                    and state.create_phase in {"verify_or_run", "fix_existing_file"}
                    and (
                        state.task_shape not in TRANSFORM_TASK_SHAPES
                        or state.large_file_mode in {"chunk", "strict_chunk"}
                    )
                ):
                    state.create_strategy = "chunked_rewrite"
                    state.create_strategy_reason = "repeated full write_file"
                    state.create_phase = "chunk_begin"
                    _reset_chunk_session(state)
                    log("CHUNK ESCALATION REASON: repeated full write_file")
                log(f"CREATE STRATEGY: {state.create_strategy}")
                log(f"CREATE PHASE: {state.create_phase}")
                if state.create_strategy == "chunked_rewrite":
                    log(
                        f"CHUNK SESSION: {'open' if state.chunk_session_open else 'closed'} "
                        f"target={state.chunk_target_path or 'app.py'} expected_parts={state.chunk_expected_parts or 0}"
                    )
                    log(f"CHUNK SESSION STATUS: received {state.chunk_received_parts or 0}/{state.chunk_expected_parts or 0} parts")
                    if state.chunk_missing_parts:
                        preview = ",".join(str(i) for i in state.chunk_missing_parts[:12])
                        suffix = "..." if len(state.chunk_missing_parts) > 12 else ""
                        log(f"CHUNK MISSING PARTS: {preview}{suffix}")
                    next_required = _chunk_next_required_action(state)
                    if next_required:
                        log(f"CHUNK NEXT REQUIRED ACTION: {next_required}")

            log(
                f"ITER {iteration + 1} | route={state.model_route}/{state.current_provider} "
                f"| reason={truncate_middle(state.route_reason or 'n/a', 120)}"
            )
            prompt_payload = build_prompt(prompt, history, state)
            log(f"PROMPT: {len(prompt_payload)} chars")
            try:
                raw = call_model(
                    prompt_payload,
                    provider_override=state.current_provider,
                    max_output_tokens=config.MAX_OUTPUT_TOKENS,
                    timeout_handler=model_timeout_handler,
                    timeout_seconds=config.MODEL_TIMEOUT,
                    stop_checker=stop_requested,
                )
            except Exception as e:
                message = str(e)
                log(message)
                history.append(message)
                if "MODEL REQUEST KILLED BY USER" in message:
                    if stop_requested():
                        log("STOP REQUESTED")
                        terminal_reason = "user stop"
                        break
                    log("MODEL REQUEST KILLED -> CONTINUE")
                    _save_session_state(state)
                    continue
                if _overflow_like(message):
                    state.prompt_compaction_level += 1
                    log("CONTEXT PRESSURE DETECTED -> COMPACTING PROMPT")
                    _save_session_state(state)
                    continue
                if _hard_rescue_handoff(state, history, log, reason=message, rescue_decider=rescue_decider):
                    _save_session_state(state)
                    continue
                if _attempt_local_fallback(state, history, log, reason=message):
                    _save_session_state(state)
                    continue
                terminal_reason = message
                break

            raw_text = str(raw)
            raw_lines = raw_text.splitlines()
            collapsed_reason = ""
            if len(raw_lines) > 24:
                collapsed_reason = f"{len(raw_lines)} lines"
            if "SECTION" in raw_text and raw_text.count("SECTION") > 12:
                collapsed_reason = (collapsed_reason + ", " if collapsed_reason else "") + "repeated token SECTION"
            try:
                raw_log_file.write("RAW RESPONSE FULL:\n" + raw_text + "\n")
                raw_log_file.flush()
            except Exception:
                pass
            if collapsed_reason:
                log(f"RAW RESPONSE COLLAPSED: {collapsed_reason} ({len(raw_text)} chars)")
            else:
                log(f"RAW RESPONSE: {truncate_middle(raw_text, 900)}")

            if stop_requested():
                log("STOP REQUESTED")
                terminal_reason = "user stop"
                break

            try:
                data = parse_response(
                    raw,
                    active_target=state.active_patch_target if state.mode == "patch" else "",
                    expected_file_count=state.expected_file_count,
                    single_file_task=state.single_file_task,
                    target_hint=(
                        state.active_patch_target
                        if state.mode == "patch"
                        else (
                            (state.target_files[0] if state.target_files else "")
                            or state.last_created_main_file
                            or state.chunk_target_path
                        )
                    ),
                )
                parse_path = str(data.get("parse_path") or "")
                if parse_path:
                    log(f"PARSE PATH: {parse_path}")
                consecutive_parse_errors = 0
            except Exception as e:
                message = str(e)
                log(message)
                history.append(_compact_parse_error_for_history(message))
                if state.mode == "patch" and state.single_file_task:
                    fallback_note = _single_file_patch_retry_note(state)
                    if fallback_note not in history[-4:]:
                        log("PATCH PARSE FALLBACK -> REQUEST ANCHORED OR CHUNKED RETRY")
                        history.append(fallback_note)
                consecutive_parse_errors += 1
                if consecutive_parse_errors >= config.MAX_PARSE_ERRORS and _hard_rescue_handoff(
                    state, history, log, reason=message, rescue_decider=rescue_decider
                ):
                    consecutive_parse_errors = 0
                    _save_session_state(state)
                    continue
                if consecutive_parse_errors >= config.MAX_PARSE_ERRORS:
                    log("STOP: repeated parse errors")
                    if _attempt_local_fallback(state, history, log, reason=message):
                        consecutive_parse_errors = 0
                        _save_session_state(state)
                        continue
                    terminal_reason = "repeated parse errors"
                    break
                continue

            if stop_requested():
                log("STOP REQUESTED")
                terminal_reason = "user stop"
                break

            actions = [_normalize_action(action, state) for action in data.get("actions", [])]
            if state.task_shape == "transform_copy_task" and state.transform_analysis_complete:
                only_reads = bool(actions) and all(str(a.get("type", "")) in {"read_file", "find_in_file"} for a in actions)
                if only_reads:
                    log("TRANSFORM LOOP GUARD 2/2 -> deterministic path")
                    log("TRANSFORM FALLBACK: builtin deterministic executor")
                    ok, written, err = _deterministic_transform_copy(state)
                    if ok:
                        log(f"TRANSFORM OUTPUTS WRITTEN: {', '.join(written)}")
                        state.transform_phase = "transform_verify"
                        state.transform_no_material_progress_streak = 0
                    else:
                        log(f"TRANSFORM FALLBACK FAILED: {err}")
                        iteration_error = err or "deterministic transform failed"
                        terminal_reason = "transform_loop_repeated_plain_read"
                        _save_session_state(state)
                        break
                    touched_paths = list(written)
                    verify_observation = verify_touched_paths(
                        touched_paths,
                        project_root=state.active_project_root,
                        smoke_run=False,
                    )
                    transform_verify = _verify_transform_outputs(state)
                    state.transform_last_verify_ok = bool(transform_verify.ok)
                    log(f"TRANSFORM VERIFY: {'pass' if transform_verify.ok else 'fail'}")
                    if not transform_verify.ok:
                        log(transform_verify.details or transform_verify.summary)
                        terminal_reason = "transform_loop_repeated_plain_read"
                        _save_session_state(state)
                        break
                    state.transform_phase = "complete"
                    success_msg = "SUCCESSFUL RUN -> STOP (transform outputs verified)"
                    log(success_msg)
                    history.append(success_msg)
                    finished_success = True
                    terminal_reason = "transform_verified_success"
                    _save_session_state(state)
                    break
            if state.mode == "patch":
                switched, reason = _negotiate_hotspot_from_actions(state, actions)
                if switched:
                    log(f"HOTSPOT NEGOTIATION: {reason}")
                    log(f"HOTSPOT PRIMARY SELECTED: {state.patch_hotspot_label or '(unknown)'}")
            guarded_actions = []
            for action in actions:
                risky, reason = _replace_in_file_looks_ungrounded(action, state)
                if risky:
                    log(reason)
                    history.append(reason)
                    guarded_actions.append(
                        _format_violation_action(
                            reason + ". PATCH MISS RECOVERY: switching to anchor-based edit."
                        )
                    )
                else:
                    guarded_actions.append(action)
            actions = guarded_actions
            plan = str(data.get("plan", ""))
            plan_fingerprint = _fingerprint_plan(plan, actions)
            requested_large_writes = sum(1 for action in actions if _is_large_write_action(action))
            chunk_ops_requested = sum(
                1 for action in actions if action.get("type") in {"begin_file_rewrite", "append_file_chunk", "finalize_file_rewrite"}
            )
            replace_block_requested = sum(1 for action in actions if action.get("type") == "replace_block")
            patch_broad_attempt = (
                state.mode == "patch"
                and (
                    replace_block_requested > 0
                    or any(action.get("type") == "patch_lines" for action in actions)
                    or requested_large_writes > 0
                )
            )

            if plan_fingerprint == state.last_plan_fingerprint:
                state.stuck_iterations += 1
            else:
                state.stuck_iterations = 0
            state.last_plan_fingerprint = plan_fingerprint

            if state.stuck_iterations >= config.STALL_TRIGGER:
                history.append("Repeated same plan without progress. Refresh from current files only.")
                if _hard_rescue_handoff(state, history, log, reason="repeated same plan", rescue_decider=rescue_decider):
                    continue
                _reset_stuck_state(state)
                if state.mode == "patch":
                    _refresh_patch_context(state)
                continue

            if not actions:
                if state.last_runtime_error:
                    msg = "EMPTY DONE BLOCKED: runtime or verify error still exists"
                    log(msg)
                    history.append(msg)
                    _save_session_state(state)
                    continue
                if state.task_shape == "transform_copy_task":
                    outputs_ok, missing = _transform_outputs_exist(state)
                    if not outputs_ok:
                        msg = f"EMPTY DONE BLOCKED: missing required transform outputs -> {', '.join(missing)}"
                        log(msg)
                        history.append(msg)
                        _save_session_state(state)
                        continue
                    if not state.transform_last_verify_ok:
                        msg = "EMPTY DONE BLOCKED: verification has not passed for transform outputs"
                        log(msg)
                        history.append(msg)
                        _save_session_state(state)
                        continue
                if config.ALLOW_EMPTY_DONE_RETRY and not empty_done_retry_used and not state.progress_happened:
                    empty_done_retry_used = True
                    msg = "EMPTY DONE TOO EARLY -> RETRY. You did nothing yet."
                    log(msg)
                    history.append(msg)
                    _save_session_state(state)
                    continue
                log("DONE")
                finished_success = True
                terminal_reason = "model returned done with no pending runtime error"
                break

            executed_count = 0
            had_run_cmd = False
            touched_paths = []
            iteration_progress = False
            iteration_error = ""
            critical_generation_failed = False
            patch_anchor_failure = False
            meaningful_materialization = False
            chunk_protocol_violation_seen = False
            blocked_plain_source_read_seen = False
            transform_material_progress = False
            transform_material_reason = ""
            stage_context = _build_atomic_patch_stage(actions, state)

            try:
                for action in actions:
                    if stop_checker and stop_checker():
                        log("STOP REQUESTED")
                        terminal_reason = "user stop"
                        break

                    if (
                        state.mode == "create"
                        and state.create_strategy == "chunked_rewrite"
                        and action.get("type") == "write_file"
                        and _is_large_write_action(action)
                    ):
                        action = _format_violation_action(
                            "CREATE STRATEGY VIOLATION: chunked_rewrite active; use begin_file_rewrite/append_file_chunk/finalize_file_rewrite"
                        )

                    if state.mode == "create" and state.create_strategy == "chunked_rewrite":
                        action_type = action.get("type")
                        action_path = normalize_rel_path(action.get("args", {}).get("path", "") or state.chunk_target_path or "app.py")
                        if not state.chunk_target_path:
                            state.chunk_target_path = action_path
                        if state.create_phase == "chunk_begin" and action_type != "begin_file_rewrite":
                            action = _format_violation_action(
                                "CHUNK PROTOCOL VIOLATION: chunk_begin phase requires begin_file_rewrite."
                            )
                        elif state.create_phase == "chunk_append" and state.chunk_finalize_pending and action_type != "finalize_file_rewrite":
                            action = _format_violation_action(
                                "CHUNK PROTOCOL VIOLATION: all parts received; finalize_file_rewrite required."
                            )
                        elif state.create_phase == "chunk_append" and action_type not in {"append_file_chunk", "finalize_file_rewrite"}:
                            action = _format_violation_action(
                                "CHUNK PROTOCOL VIOLATION: chunk_append phase expects append_file_chunk (or finalize_file_rewrite when complete)."
                            )
                        elif state.create_phase == "chunk_append" and action_type == "finalize_file_rewrite" and not state.chunk_finalize_pending:
                            action = _format_violation_action(
                                "CHUNK PROTOCOL VIOLATION: finalize_file_rewrite is too early; append missing parts first."
                            )
                        elif state.create_phase == "chunk_finalize" and action_type != "finalize_file_rewrite":
                            action = _format_violation_action(
                                "CHUNK PROTOCOL VIOLATION: chunk_finalize phase requires finalize_file_rewrite."
                            )
                        if action_type in {"begin_file_rewrite", "append_file_chunk", "finalize_file_rewrite"}:
                            if state.chunk_target_path and os.path.normcase(os.path.normpath(action_path)) != os.path.normcase(os.path.normpath(state.chunk_target_path)):
                                action = _format_violation_action(
                                    f"CHUNK PROTOCOL VIOLATION: target drift ({action_path}) != active chunk target ({state.chunk_target_path})"
                                )
                            elif action_type == "begin_file_rewrite" and state.chunk_session_open:
                                action = _format_violation_action(
                                    "CHUNK PROTOCOL VIOLATION: repeated begin_file_rewrite for open session. Continue with append_file_chunk/finalize_file_rewrite."
                                )
                            elif state.create_phase == "chunk_append" and action_type == "begin_file_rewrite":
                                action = _format_violation_action(
                                    "CHUNK PROTOCOL VIOLATION: phase chunk_append requires append_file_chunk or finalize_file_rewrite."
                                )
                            elif state.create_phase == "chunk_finalize" and action_type in {"begin_file_rewrite", "append_file_chunk"}:
                                action = _format_violation_action(
                                    "CHUNK PROTOCOL VIOLATION: phase chunk_finalize requires finalize_file_rewrite."
                                )

                    if (
                        state.mode == "patch"
                        and state.patch_strategy == "chunked_rewrite_existing_file"
                        and action.get("type") in {"write_file", "replace_in_file", "insert_before", "insert_after", "replace_block", "patch_lines"}
                    ):
                        action = _format_violation_action(
                            "PATCH STRATEGY VIOLATION: chunked_rewrite_existing_file active; use begin_file_rewrite/append_file_chunk/finalize_file_rewrite on active_patch_target"
                        )
                    if state.mode == "patch" and _action_outside_grounded_hotspots(action, state):
                        action = _format_violation_action(
                            "GROUNDED HOTSPOT VIOLATION: patch action drifted outside primary/secondary grounded candidates. "
                            "Retry using selected hotspot anchors/snippet."
                        )

                    action_key = json.dumps(action, ensure_ascii=False, sort_keys=True)
                    repeated_count = state.duplicate_action_cache.get(action_key, 0)
                    if repeated_count >= config.REPEAT_ACTION_LIMIT:
                        msg = f"BLOCK REPEATED ACTION: {action.get('type')}"
                        log(msg)
                        history.append(msg)
                        history.append(
                            "REPEATED ACTION ESCALATION: same failing action exceeded retry budget. "
                            "Switch strategy/model instead of repeating unchanged call."
                        )
                        run_blocked_actions += 1
                        if state.mode == "patch":
                            _refresh_patch_context(state)
                        state.stuck_iterations = max(state.stuck_iterations + 1, config.STALL_TRIGGER)
                        iteration_error = msg
                        critical_generation_failed = True
                        break

                    created_by_action = False
                    if action.get("type") in {"write_file", "finalize_file_rewrite"}:
                        rel_path = action.get("args", {}).get("path", "")
                        abs_path = os.path.abspath(os.path.join(state.active_project_root, rel_path))
                        created_by_action = not os.path.exists(abs_path)

                    action_root = _stage_action_project_root(action, state, stage_context)
                    staged_action = action_root != state.active_project_root
                    observation = None
                    if _is_transform_plain_source_read(action, state) and state.transform_source_read_seen:
                        blocked_plain_source_read_seen = True
                        run_blocked_actions += 1
                        log(f"BLOCK REPEATED PLAIN SOURCE READ: {state.transform_primary_source}")
                        history.append(f"BLOCK REPEATED PLAIN SOURCE READ: {state.transform_primary_source}")
                        continue
                    if (
                        action.get("type") == "read_file"
                        and state.task_shape in TRANSFORM_TASK_SHAPES
                        and state.large_file_mode in {"enabled", "chunk", "strict_chunk"}
                    ):
                        from contracts import Observation

                        rel_path = normalize_rel_path(action.get("args", {}).get("path", ""))
                        targeted_read = bool(
                            action.get("args", {}).get("section_id")
                            or action.get("args", {}).get("line_start")
                            or action.get("args", {}).get("line_end")
                            or action.get("args", {}).get("around_anchor")
                            or action.get("args", {}).get("query")
                        )
                        cached = state.large_read_cache.get(rel_path) if rel_path else None
                        current_sig = _file_signature(state.active_project_root, rel_path)
                        cache_valid = (
                            isinstance(cached, dict)
                            and bool(cached.get("details"))
                            and str(cached.get("signature") or "") == str(current_sig or "")
                        )
                        if cache_valid and not targeted_read:
                            observation = Observation(
                                True,
                                f"read_file cached transport {rel_path}",
                                changed=False,
                                details=truncate_middle(str(cached.get("details", "")), 2200),
                                tool="read_file",
                                path=os.path.abspath(os.path.join(state.active_project_root, rel_path)),
                            )
                        else:
                            observation = _execute_action(
                                action,
                                state,
                                project_root_override=action_root,
                                stop_checker=stop_requested,
                            )
                            if not targeted_read:
                                transport_text, cache_entry = _structured_large_read_observation(
                                    rel_path,
                                    state.active_project_root,
                                    large_mode=state.large_file_mode,
                                )
                                if transport_text and isinstance(cache_entry, dict):
                                    state.large_read_cache[rel_path] = cache_entry
                                    observation.summary = str(cache_entry.get("summary") or observation.summary)
                                    observation.details = truncate_middle(
                                        str(cache_entry.get("details") or observation.details or ""),
                                        2200,
                                    )
                    else:
                        observation = _execute_action(
                            action,
                            state,
                            project_root_override=action_root,
                            stop_checker=stop_requested,
                        )
                    if staged_action:
                        metadata = dict(observation.metadata or {})
                        metadata["touches_file"] = False
                        metadata["staged"] = True
                        observation.metadata = metadata
                        observation.summary = observation.summary + " [staged]"

                    executed_count += 1
                    run_executed_actions += 1
                    log(observation.summary)
                    if observation.details:
                        log(observation.details)
                    state.last_useful_observation_summary = observation.summary

                    history.append(_format_history_observation(observation.summary, observation.details))
                    if (
                        observation.tool == "read_file"
                        and state.task_shape in TRANSFORM_TASK_SHAPES
                        and state.large_file_mode in {"enabled", "chunk", "strict_chunk"}
                    ):
                        history.append(
                            "LARGE READ CACHE: use section index + targeted excerpts from cached transport. "
                            "Do not repeat identical full-file read_file."
                        )

                    if (
                        state.mode == "create"
                        and state.create_strategy == "chunked_rewrite"
                        and observation.tool == "action_format_violation"
                        and "CHUNK PROTOCOL VIOLATION" in str(observation.details or "")
                    ):
                        chunk_protocol_violation_seen = True
                        history.append(
                            "CHUNK PROTOCOL GUIDANCE STRENGTHENED: previous response violated phase contract. "
                            "Continue with CHUNK NEXT REQUIRED ACTION only."
                        )
                        log("CHUNK PROTOCOL GUIDANCE STRENGTHENED")

                    progress_signal = observation.changed and (
                        observation.tool in PROGRESS_TOOLS
                        or bool((observation.metadata or {}).get("progress"))
                    )
                    if progress_signal:
                        iteration_progress = True
                        state.progress_happened = True
                        state.duplicate_action_cache.clear()
                        state.local_fallback_step = 0
                    else:
                        state.duplicate_action_cache[action_key] = repeated_count + 1

                    if observation.ok and (
                        bool((observation.metadata or {}).get("touches_file"))
                        or (observation.changed and observation.tool in PROGRESS_TOOLS)
                    ):
                        meaningful_materialization = True

                    if observation.tool == "run_cmd":
                        had_run_cmd = True
                        if not observation.ok:
                            iteration_error = observation.details or observation.summary
                        else:
                            meaningful_materialization = True

                    if state.task_shape == "transform_copy_task" and observation.ok:
                        tool = str(observation.tool or "")
                        args = action.get("args", {}) if isinstance(action.get("args"), dict) else {}
                        rel_path = normalize_rel_path(args.get("path", ""))
                        targeted_read = bool(
                            args.get("section_id")
                            or args.get("line_start")
                            or args.get("line_end")
                            or args.get("around_anchor")
                            or args.get("query")
                        )
                        if tool in {"write_file", "finalize_file_rewrite"} and rel_path in (state.derived_allowed_files or []):
                            transform_material_progress = True
                            transform_material_reason = f"derived output write: {rel_path}"
                        elif tool == "read_file" and targeted_read:
                            transform_material_progress = True
                            transform_material_reason = "targeted section read"
                        elif tool == "find_in_file" and str(args.get("query", "")).strip():
                            transform_material_progress = True
                            transform_material_reason = "targeted query"
                        elif tool == "run_cmd":
                            cmd_text = _normalize_prompt_text(args.get("cmd", ""))
                            if "transform" in cmd_text and "helper" in cmd_text:
                                transform_material_progress = True
                                transform_material_reason = "helper transform script run"

                    if state.task_shape == "transform_copy_task" and _is_transform_plain_source_read(action, state) and observation.ok:
                        state.transform_source_read_seen = True
                        if not state.transform_analysis_complete:
                            state.transform_analysis_complete = True
                            state.transform_phase = "transform_write_outputs"
                            log("TRANSFORM PHASE: transform_write_outputs")

                    if state.mode == "create" and state.create_strategy == "chunked_rewrite":
                        if observation.tool == "begin_file_rewrite" and observation.ok:
                            expected_raw = action.get("args", {}).get("expected_parts")
                            try:
                                expected_val = int(expected_raw)
                            except Exception:
                                expected_val = 0
                            state.chunk_session_open = True
                            state.chunk_target_path = normalize_rel_path(action.get("args", {}).get("path", "") or state.chunk_target_path or "app.py")
                            state.chunk_expected_parts = max(0, expected_val)
                            state.chunk_received_parts = 0
                            state.chunk_missing_parts = list(range(1, state.chunk_expected_parts + 1))
                            state.chunk_finalize_pending = False
                            state.create_phase = "chunk_append"
                            state.chunk_protocol_violation_streak = 0
                            log(f"CHUNK PHASE: {state.create_phase}")
                        elif observation.tool == "append_file_chunk" and observation.ok:
                            summary_text = str(observation.summary or "")
                            match = CHUNK_STATUS_RE.search(summary_text)
                            if match:
                                try:
                                    expected_val = int(match.group(2))
                                    received_val = int(match.group(3))
                                except Exception:
                                    expected_val = state.chunk_expected_parts
                                    received_val = state.chunk_received_parts
                                state.chunk_session_open = True
                                state.chunk_expected_parts = max(state.chunk_expected_parts, expected_val)
                                state.chunk_received_parts = max(state.chunk_received_parts, received_val)
                            rewrite_data = _load_rewrite_state_for(state.chunk_target_path, state.active_project_root)
                            if isinstance(rewrite_data, dict):
                                missing_parts = _compute_missing_parts(state.chunk_expected_parts, rewrite_data.get("parts"))
                                state.chunk_missing_parts = missing_parts
                            state.create_phase = "chunk_finalize" if (
                                state.chunk_expected_parts > 0 and state.chunk_received_parts >= state.chunk_expected_parts
                            ) else "chunk_append"
                            state.chunk_finalize_pending = state.create_phase == "chunk_finalize"
                            state.chunk_protocol_violation_streak = 0
                            log(f"CHUNK SESSION STATUS: received {state.chunk_received_parts}/{state.chunk_expected_parts} parts")
                            log(f"CHUNK PHASE: {state.create_phase}")
                        elif observation.tool == "finalize_file_rewrite" and observation.ok:
                            state.chunk_session_open = False
                            state.chunk_finalize_pending = False
                            state.chunk_received_parts = 0
                            state.chunk_expected_parts = 0
                            state.chunk_missing_parts = []
                            state.create_phase = "verify_or_run"
                            state.chunk_protocol_violation_streak = 0
                            meaningful_materialization = True
                            log("CHUNK PHASE: verify_or_run")

                    metadata = observation.metadata or {}
                    touches_file = False
                    if not metadata.get("staged"):
                        touches_file = observation.tool in PROGRESS_TOOLS or bool(metadata.get("touches_file"))
                    if touches_file and observation.ok:
                        rel_path = action.get("args", {}).get("path", "")
                        if rel_path:
                            touched_paths.append(rel_path)
                            _append_unique(state.touched_files, rel_path)
                            state.last_written_files.append(rel_path)
                            if action.get("type") in {"write_file", "finalize_file_rewrite"} and created_by_action:
                                _append_unique(state.created_files, rel_path)

                    if observation.tool in {"replace_in_file", "insert_before", "insert_after", "replace_block", "patch_lines", "finalize_file_rewrite"} and not observation.ok:
                        if state.mode == "patch":
                            target_path = action.get("args", {}).get("path", state.active_patch_target)
                            fresh_root = action_root if staged_action else state.active_project_root
                            fresh = read_file_snippet(
                                target_path,
                                project_root=fresh_root,
                                max_lines=config.PATCH_SNIPPET_LINES,
                            )
                            history.append("Fresh file snapshot after failed patch:\n" + truncate_middle(fresh, 1200))
                            details_lower = (observation.details or "").lower()
                            if observation.tool == "replace_in_file" and ("not found" in details_lower or "miss" in _normalize_prompt_text(observation.summary)):
                                state.patch_replace_miss_streak += 1
                                if state.patch_replace_miss_streak >= 2:
                                    state.patch_phase = "inspect_target"
                                    state.patch_exact_snippet = fresh
                                    log("PATCH MISS RECOVERY: switching to anchor-based edit")
                                    promoted, transition = _promote_hotspot_candidate(state, "repeated replace_in_file miss")
                                    if promoted:
                                        log("HOTSPOT DOWNGRADED: repeated miss")
                                        log(f"HOTSPOT FAILOVER: {transition}")
                                    history.append(
                                        "PATCH MISS RECOVERY: repeated replace_in_file miss. "
                                        "Use replace_block/insert_before/insert_after with real anchors from fresh snippet."
                                    )
                                    if state.patch_stale_error_detected:
                                        log("PATCH GROUNDING REFRESH: stale-context miss")
                                        history.append(
                                            "PATCH CONTEXT PRIORITY: current snapshot > stale logs. "
                                            "Older traceback hints appear stale; ground next patch only on current snippet/anchors."
                                        )
                            if "anchor not found" in details_lower or "missing anchor" in details_lower:
                                patch_anchor_failure = True
                                grounded = _grounded_patch_retry_context(target_path, state.active_project_root, prompt)
                                state.patch_phase = "inspect_target"
                                promoted, transition = _promote_hotspot_candidate(state, "missing anchor on current hotspot")
                                if promoted:
                                    log("HOTSPOT DOWNGRADED: repeated miss")
                                    log(f"HOTSPOT FAILOVER: {transition}")
                                log("PATCH GROUNDING REFRESH: missing anchor")
                                log("GROUNDING RETRY WITH REAL OUTLINE/ANCHORS")
                                history.append("Grounded retry context after missing anchor:\n" + truncate_middle(grounded, 2200))

                    if not observation.ok and not iteration_error:
                        iteration_error = observation.details or observation.summary
                    if observation.tool in {
                        "write_file",
                        "begin_file_rewrite",
                        "append_file_chunk",
                        "finalize_file_rewrite",
                        "replace_in_file",
                        "insert_before",
                        "insert_after",
                        "replace_block",
                        "patch_lines",
                    } and not observation.ok:
                        critical_generation_failed = True
                    if observation.tool in {"off_target_patch_action", "action_format_violation"}:
                        run_blocked_actions += 1
                        break
                    if critical_generation_failed:
                        break

                    _save_session_state(state)

                if not iteration_error:
                    committed_paths, created_files, commit_error = _commit_atomic_patch_stage(stage_context, state, log)
                    for rel_path in committed_paths:
                        touched_paths.append(rel_path)
                        _append_unique(state.touched_files, rel_path)
                        state.last_written_files.append(rel_path)
                    for rel_path in created_files:
                        _append_unique(state.created_files, rel_path)
                    if commit_error:
                        iteration_error = commit_error
                    _save_session_state(state)
            finally:
                _cleanup_atomic_patch_stage(stage_context)

            if stop_requested():
                log("STOP REQUESTED")
                terminal_reason = "user stop"
                break

            if state.task_shape == "transform_copy_task":
                if transform_material_progress:
                    state.transform_no_material_progress_streak = 0
                    log(f"MATERIAL PROGRESS: yes ({transform_material_reason or 'transform change'})")
                    if state.transform_phase == "transform_analyze":
                        state.transform_phase = "transform_write_outputs"
                        state.transform_analysis_complete = True
                        log("TRANSFORM PHASE: transform_write_outputs")
                else:
                    state.transform_no_material_progress_streak += 1
                    log("MATERIAL PROGRESS: no")

                outputs_ok, missing_outputs = _transform_outputs_exist(state)
                if (
                    state.transform_source_read_seen
                    and not outputs_ok
                    and state.transform_no_material_progress_streak >= 2
                ):
                    state.route_reason = "repeated_plain_read_transform_loop"
                    state.local_fallback_step = max(state.local_fallback_step, 3)
                    log("TRANSFORM LOOP GUARD 2/2 -> deterministic path")
                    history.append("TRANSFORM LOOP GUARD 2/2 -> deterministic path")
                    log("TRANSFORM FALLBACK: builtin deterministic executor")
                    ok, written, err = _deterministic_transform_copy(state)
                    if ok:
                        log(f"TRANSFORM OUTPUTS WRITTEN: {', '.join(written)}")
                        touched_paths = list(written)
                        verify_observation = verify_touched_paths(
                            touched_paths,
                            project_root=state.active_project_root,
                            smoke_run=False,
                        )
                        transform_verify = _verify_transform_outputs(state)
                        state.transform_last_verify_ok = bool(transform_verify.ok)
                        log(f"TRANSFORM VERIFY: {'pass' if transform_verify.ok else 'fail'}")
                        if not transform_verify.ok:
                            log(transform_verify.details or transform_verify.summary)
                            terminal_reason = "transform_loop_repeated_plain_read"
                            _save_session_state(state)
                            break
                        state.transform_phase = "complete"
                        state.transform_no_material_progress_streak = 0
                        success_msg = "SUCCESSFUL RUN -> STOP (transform outputs verified)"
                        log(success_msg)
                        history.append(success_msg)
                        finished_success = True
                        terminal_reason = "transform_verified_success"
                        _save_session_state(state)
                        break
                    if _attempt_local_fallback(state, history, log, reason="repeated_plain_read_transform_loop"):
                        _save_session_state(state)
                        continue
                    terminal_reason = "transform_loop_repeated_plain_read"
                    break
                elif (
                    state.transform_source_read_seen
                    and not outputs_ok
                    and state.transform_no_material_progress_streak == 1
                ):
                    log("TRANSFORM LOOP GUARD 1/2")

            if executed_count == 0:
                if state.task_shape == "transform_copy_task" and blocked_plain_source_read_seen:
                    _save_session_state(state)
                    continue
                msg = "NO EXECUTED ACTIONS"
                log(msg)
                history.append(msg)
                if _hard_rescue_handoff(state, history, log, reason=msg, rescue_decider=rescue_decider):
                    _save_session_state(state)
                    continue
                if _attempt_local_fallback(state, history, log, reason=msg):
                    _save_session_state(state)
                    continue
                terminal_reason = msg
                break

            verify_observation = verify_touched_paths(
                touched_paths,
                project_root=state.active_project_root,
                smoke_run=config.AUTO_SMOKE_RUN and not had_run_cmd,
            )
            if config.AUTO_VERIFY_PYTHON and touched_paths:
                log(verify_observation.summary)
                if verify_observation.details:
                    log(verify_observation.details)
                state.last_useful_observation_summary = verify_observation.summary
                history.append(_format_history_observation(verify_observation.summary, verify_observation.details))
                if not verify_observation.ok:
                    iteration_error = verify_observation.details or verify_observation.summary
                elif state.mode == "patch":
                    state.patch_phase = "verify_patch"
            if state.task_shape == "transform_copy_task":
                transform_verify = _verify_transform_outputs(state)
                log(transform_verify.summary)
                if transform_verify.details:
                    log(transform_verify.details)
                history.append(_format_history_observation(transform_verify.summary, transform_verify.details))
                state.transform_last_verify_ok = bool(transform_verify.ok)
                if not transform_verify.ok:
                    iteration_error = iteration_error or (transform_verify.details or transform_verify.summary)

            if iteration_error:
                state.last_runtime_error = iteration_error
                if state.task_shape == "transform_copy_task":
                    state.transform_last_verify_ok = False
                if state.mode == "create" and (state.last_written_files or state.created_files):
                    state.create_phase = "fix_existing_file"
                if state.mode == "create" and state.create_strategy == "chunked_rewrite":
                    err_lower = _normalize_prompt_text(iteration_error)
                    if "missing rewrite state" in err_lower or "corrupted rewrite state" in err_lower:
                        reason = "stale or missing rewrite state during chunk protocol"
                        log("CHUNK SESSION RESET: stale state after restart")
                        log(f"CHUNK SESSION RESET REASON: {reason}")
                        _reset_chunk_session(state, reason=reason, clear_rewrite_state=True)
                        state.create_phase = "chunk_begin"
                        log("CHUNK PHASE: chunk_begin")
                    elif chunk_protocol_violation_seen and state.chunk_session_open and state.chunk_expected_parts > 0 and state.chunk_received_parts == 0:
                        state.chunk_protocol_violation_streak += 1
                        if state.chunk_protocol_violation_streak >= 2:
                            reason = (
                                "repeated protocol violations with no chunk progress "
                                f"(0/{state.chunk_expected_parts})"
                            )
                            log("CHUNK LOOP BREAKER: repeated protocol violations with no chunk progress")
                            if state.model_route in {"local/default", "standard"}:
                                state.stuck_iterations = max(state.stuck_iterations, 2)
                                log("CHUNK ROUTE ESCALATION REASON: repeated chunk protocol failure")
                            log(f"CHUNK FAILURE REASON: {reason}")
                            log("CHUNK FALLBACK: reverting to write_file after protocol failure")
                            _reset_chunk_session(state, reason=reason, clear_rewrite_state=True)
                            state.create_strategy = "write_file"
                            state.create_strategy_reason = "chunk protocol failure fallback"
                            state.create_full_write_streak = 0
                            state.create_phase = "fix_existing_file" if (state.last_written_files or state.created_files) else "initial_create"
                            log(f"CREATE STRATEGY: {state.create_strategy}")
                            log(f"CREATE STRATEGY REASON: {state.create_strategy_reason}")
                if state.mode == "patch":
                    state.patch_failure_streak += 1
                    if state.patch_stale_error_detected:
                        state.patch_phase = "inspect_target"
                        history.append(
                            "PATCH GROUNDING REFRESH: prioritize current file snapshot. "
                            "Stale traceback/log context downgraded for next retry."
                        )
                    if patch_anchor_failure:
                        state.patch_anchor_failure_streak += 1
                    if patch_broad_attempt:
                        state.patch_broad_patch_streak += 1
                    if _is_syntax_like_failure(iteration_error):
                        state.patch_syntax_failure_streak += 1
                    if (
                        verify_observation
                        and not verify_observation.ok
                        and (
                            _is_syntax_like_failure(verify_observation.details or "")
                            or _is_syntax_like_failure(verify_observation.summary or "")
                        )
                    ):
                        state.patch_verify_failure_streak += 1
                    state.patch_phase = "inspect_target"
                    strategy_escalation_reason = _maybe_escalate_patch_strategy(state)
                    if strategy_escalation_reason:
                        log(f"PATCH REWRITE ESCALATION REASON: {strategy_escalation_reason}")
                log("ITERATION FAILED -> CONTINUE")
                _save_session_state(state)
                continue

            state.last_runtime_error = ""

            if state.mode == "create":
                if had_run_cmd and verify_observation.ok:
                    state.create_phase = "complete"
                elif touched_paths and verify_observation.ok:
                    if state.create_phase == "initial_create":
                        state.create_phase = "verify_or_run"

                if requested_large_writes > 0 and not had_run_cmd:
                    state.create_full_write_streak += 1
                else:
                    state.create_full_write_streak = 0

                should_escalate = False
                escalation_reason = ""
                if state.create_strategy != "chunked_rewrite":
                    if state.task_shape in TRANSFORM_TASK_SHAPES:
                        if state.large_file_mode in {"chunk", "strict_chunk"} and state.create_full_write_streak >= 3:
                            should_escalate = True
                            escalation_reason = "large transform repeated full write_file"
                        elif state.large_file_mode in {"chunk", "strict_chunk"} and state.stuck_iterations >= 2 and requested_large_writes > 0:
                            should_escalate = True
                            escalation_reason = "transform no execution progress with large rewrite"
                    else:
                        if state.create_full_write_streak >= 2:
                            should_escalate = True
                            escalation_reason = "repeated full write_file"
                        elif state.stuck_iterations >= 1 and requested_large_writes > 0:
                            should_escalate = True
                            escalation_reason = "no execution progress with large rewrite"
                if should_escalate:
                    state.create_strategy = "chunked_rewrite"
                    state.create_strategy_reason = escalation_reason
                    state.create_phase = "chunk_begin"
                    _reset_chunk_session(state)
                    log(f"CHUNK ESCALATION REASON: {escalation_reason}")
                elif state.create_strategy == "chunked_rewrite" and chunk_ops_requested == 0 and requested_large_writes == 0 and had_run_cmd:
                    state.create_strategy = "write_file"
                    state.create_strategy_reason = "execution reached; normal flow"
                    _reset_chunk_session(state, reason="chunk flow complete")

            if state.mode == "patch":
                state.patch_failure_streak = 0
                state.patch_verify_failure_streak = 0
                state.patch_syntax_failure_streak = 0
                state.patch_anchor_failure_streak = 0
                state.patch_replace_miss_streak = 0
                if not patch_broad_attempt:
                    state.patch_broad_patch_streak = 0

            if meaningful_materialization and config.AUTO_GIT_CHECKPOINTS:
                ok, msg = git_checkpoint(
                    state.active_project_root,
                    f"checkpoint iter {iteration + 1}: {state.mode}",
                )
                log("GIT CHECKPOINT " + ("OK" if ok else "ERR"))
                if msg:
                    log(msg)

            should_stop, stop_reason = _completion_decision(
                state,
                plan,
                had_run_cmd=had_run_cmd,
                touched_paths=touched_paths,
                verify_observation=verify_observation,
                meaningful_materialization=meaningful_materialization,
            )
            if should_stop and state.task_shape == "transform_copy_task":
                outputs_ok, missing = _transform_outputs_exist(state)
                if not outputs_ok:
                    should_stop = False
                    stop_reason = f"TRANSFORM DONE BLOCKED: missing outputs -> {', '.join(missing)}"
                    log(stop_reason)
                    history.append(stop_reason)
                elif not state.transform_last_verify_ok:
                    should_stop = False
                    stop_reason = "TRANSFORM DONE BLOCKED: verify has not passed for required outputs"
                    log(stop_reason)
                    history.append(stop_reason)
            if should_stop:
                if state.mode == "patch":
                    state.patch_phase = "complete"
                log(stop_reason)
                _save_session_state(state)
                finished_success = True
                terminal_reason = stop_reason
                break

            if state.task_shape == "transform_copy_task" and not state.transform_last_verify_ok:
                iteration_progress = False
            if iteration_progress:
                state.no_progress_streak = 0
                log("PROGRESS APPLIED -> CONTINUE")
            else:
                state.no_progress_streak += 1
                log("NO REAL PROGRESS -> CONTINUE")
                if state.no_progress_streak >= 2:
                    if state.mode == "create" and state.create_strategy == "chunked_rewrite":
                        log("NO-PROGRESS SHIFT: chunked_rewrite -> write_file")
                        state.create_strategy = "write_file"
                        state.create_strategy_reason = "no-progress strategy shift"
                        state.create_phase = "fix_existing_file" if (state.last_written_files or state.created_files) else "initial_create"
                        _reset_chunk_session(state, reason="no-progress strategy shift", clear_rewrite_state=False)
                    elif state.mode == "patch":
                        log("NO-PROGRESS SHIFT: refresh patch grounding")
                        state.patch_phase = "inspect_target"
                if state.no_progress_streak >= 4:
                    fail_reason = "repeated no real progress"
                    if _hard_rescue_handoff(state, history, log, reason=fail_reason, rescue_decider=rescue_decider):
                        state.no_progress_streak = 0
                        _save_session_state(state)
                        continue
                    if _attempt_local_fallback(state, history, log, reason=fail_reason):
                        state.no_progress_streak = 0
                        _save_session_state(state)
                        continue
                    log("STOP: no progress after local fallback exhaustion")
                    _save_session_state(state)
                    terminal_reason = "repeated no real progress"
                    break
            _save_session_state(state)

        output_status = "(none)"
        if state.task_shape == "transform_copy_task":
            outputs_ok, missing = _transform_outputs_exist(state)
            if outputs_ok:
                output_status = "outputs=ok"
            else:
                output_status = f"outputs_missing={','.join(missing)}"
        elif state.touched_files:
            output_status = f"outputs={len(state.touched_files)} touched"
        log(
            f"RUN SUMMARY: executed={run_executed_actions} blocked={run_blocked_actions} "
            f"stop_reason={terminal_reason or state.last_runtime_error or 'n/a'} "
            f"status={'success' if finished_success else 'stopped'} {output_status}"
        )
        flush_pending_log()

        if not finished_success:
            blocked = [
                line for line in history[-14:]
                if any(token in _normalize_prompt_text(line) for token in ("blocked", "violation", "missing", "failed", "rejected"))
            ]
            blocked_preview = " | ".join(blocked[-4:]) if blocked else "(none)"
            log("FAILURE SUMMARY:")
            log(f"- detected task shape: {state.task_shape}")
            log(f"- mode: {state.mode}")
            log(f"- route/provider: {state.model_route} ({state.current_provider})")
            log(f"- rescue status: {_rescue_mode()} ({_rescue_suppressed_reason() if _rescue_mode() != 'ON' else 'enabled'})")
            log(f"- terminal reason: {terminal_reason or state.last_runtime_error or 'max iterations or unresolved state'}")
            next_route_hint = "standard create"
            if state.task_shape in TRANSFORM_TASK_SHAPES:
                next_route_hint = "transform_copy_task with tool-first bounded analysis"
            elif state.mode == "patch":
                next_route_hint = "inspect_target -> surgical_patch with grounded anchors"
            log(f"- best next auto route: {next_route_hint}")
            if state.task_shape in TRANSFORM_TASK_SHAPES:
                source_preview = ", ".join(state.source_readonly_files[:8]) if state.source_readonly_files else "(none)"
                derived_preview = ", ".join(state.derived_allowed_files[:8]) if state.derived_allowed_files else "(none)"
                log(f"- source read-only: {source_preview}")
                log(f"- derived allowed: {derived_preview}")
            log(f"- blocked/failed observations: {truncate_middle(blocked_preview, 600)}")

        log(f"LOG SAVED TO: {log_path}")
        flush_pending_log()
        _save_session_state(state)
        return log_path

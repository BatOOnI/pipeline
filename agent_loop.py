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
from utils import ensure_gitignore, is_subpath, read_json_file, truncate_middle, write_json_file


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
PATCH_DONE_KEYWORDS = ("done", "complete", "completed", "finished", "ready", "gotowe", "zakonczone")
SESSION_DIR_RE = re.compile(r"^TEST-(\d+)$", re.IGNORECASE)
FILE_TOKEN_RE = re.compile(r"([A-Za-z0-9_./\\-]+\.[A-Za-z0-9_+-]+)")
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
ATOMIC_PATCH_FILE_ACTIONS = {
    "write_file",
    "replace_in_file",
    "insert_before",
    "insert_after",
    "replace_block",
    "patch_lines",
}


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


def _infer_mode(user_prompt):
    if config.PATCH_FILES.strip():
        return "patch"
    lower = _normalize_prompt_text(user_prompt)
    if any(keyword in lower for keyword in PATCH_KEYWORDS):
        return "patch"
    if "dodaj" in lower and _prompt_mentions_existing_file(user_prompt):
        return "patch"
    if any(keyword in lower for keyword in CREATE_KEYWORDS):
        return "create"
    return "create"


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
    }
    write_json_file(_session_path(), payload)


def _build_state(user_prompt):
    session_data = _load_session_state()
    mode = _infer_mode(user_prompt)
    previous_prompt = str(session_data.get("prompt") or session_data.get("goal") or "")
    prompt_changed = bool(previous_prompt and previous_prompt.strip() != (user_prompt or "").strip())
    session_root = str(session_data.get("project_root", "")).strip()
    configured_root = _explicit_configured_root_path()
    explicit_gui_root = bool(configured_root)
    if explicit_gui_root and (mode == "create" or os.path.isdir(configured_root)):
        active_project_root = configured_root
    elif mode != "create" and session_root and os.path.isdir(session_root):
        active_project_root = session_root
    else:
        active_project_root = _choose_project_root(mode)
    state = PipelineState(
        goal=user_prompt,
        mode=mode,
        active_project_root=active_project_root,
        current_provider=config.PROVIDER,
        prompt_changed=prompt_changed,
    )
    _hydrate_state_from_session(state, session_data)
    config.ACTIVE_PROJECT_ROOT = state.active_project_root

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
    else:
        state.target_files = target_files
        state.expected_file_count = max(1, len(target_files) or 1)
        state.single_file_task = len(target_files) <= 1

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
    last_error="",
    history=None,
    compact_level=0,
):
    target_files = target_files or []
    history = history or []
    compact_history = _compact_history(history, compact_level)

    if mode == "patch":
        return [
            "You are a coding agent working inside an existing local project.",
            f"Project root: {project_root}",
            "Mode: patch",
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
            "- For single-file patch tasks, use patch-first strategy in this order: replace_in_file, insert_before, insert_after, replace_block, write_file fallback only if necessary.",
            "- Prefer 1-3 small anchored edits for weaker local models.",
            "- Use only one strategy family per iteration. Do not mix anchored edits with rewrite strategy in the same reply unless absolutely necessary.",
            "- Use ONLY anchors that appear in the provided real outline, real anchors, or focused snippets.",
            "- Prefer exact anchor strings copied from the provided file data. Do not invent function or method names.",
            "- Do not use line_number, start_line, or end_line unless absolutely necessary.",
            "- Use patch_lines only as a legacy fallback.",
            "- Use write_file only for small full-file rewrites.",
            "- If full rewrite is needed for a large file, use chunked rewrite protocol: begin_file_rewrite, then append_file_chunk parts in order, then finalize_file_rewrite.",
            "- For Python replace_block on methods/classes, return a complete valid block body with correct indentation.",
            "- Do not return truncated or partial method fragments.",
            "- Prefer fewer, self-contained method/class replacements over many tiny risky edits.",
            "- If replacing a Python method, include the full method implementation for that method in one coherent block.",
            "- Do not create folders or new files unless the user clearly asked for that.",
            "- Do not overwrite the whole file with a tiny fragment.",
            "Examples:",
            '{"type":"insert_after","args":{"path":"app.py","anchor":"def main():\\n","content":"    print(\\"ready\\")\\n"}}',
            '{"type":"insert_before","args":{"path":"app.py","anchor":"if __name__ == \\"__main__\\":\\n","content":"\\n# launcher\\n"}}',
            '{"type":"replace_block","args":{"path":"app.py","start_anchor":"    old = 1\\n","end_anchor":"    print(old)\\n","content":"    old = 2\\n    bonus = 3\\n"}}',
            "",
            "Real file outline:",
            file_outline or "(outline unavailable)",
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

    return [
        "You are a coding agent.",
        f"Project root: {project_root}",
        "Mode: create",
        f"User task: {user_prompt}",
        "",
        "Return ONLY JSON with this exact shape:",
        '{"plan":"short plan","reasoning_short":"short reason","actions":[{"type":"write_file","args":{"path":"app.py","content":"print(\\"Hello World\\")"}}]}',
        "",
            "Allowed actions: write_file, replace_in_file, insert_before, insert_after, replace_block, begin_file_rewrite, append_file_chunk, finalize_file_rewrite, patch_lines, find_in_file, run_cmd, mkdir",
            "Rules:",
            "- All file paths must stay inside the project root.",
            "- Prefer writing project files inside this run's folder.",
            "- For Python on Windows, use python, not python3.",
            '- For Windows launcher files (.bat/.cmd), use real newlines and prefer: "@echo off", `cd /d "%~dp0"`, `py -3 app.py` with fallback to `python app.py`, and `pause`.',
            "- If a previous attempt failed, fix it instead of repeating the same broken action.",
            "- If task is complete, return plan=done with empty actions.",
            "",
            "Recent observations:",
            compact_history or "None",
    ]


def build_prompt(user_prompt, history, state):
    snippet_lines, _, _ = _prompt_budget(state.prompt_compaction_level)
    file_snippet = ""
    file_outline = ""
    real_anchors = ""
    if state.mode == "patch" and state.active_patch_target:
        outline_items = _discover_file_outline(state.active_patch_target, state.active_project_root)
        anchor_items = _discover_real_anchors(state.active_patch_target, state.active_project_root)
        file_outline = "\n".join(f"- L{line_no}: {text}" for line_no, text in outline_items[:18])
        real_anchors = "\n".join(f'- "{text}"' for text in anchor_items[:12])
        file_snippet = _focused_patch_snippets(
            state.active_patch_target,
            state.active_project_root,
            user_prompt,
            snippet_lines,
        )

    bits = build_instruction_bits(
        user_prompt,
        mode=state.mode,
        project_root=state.active_project_root,
        target_files=state.target_files,
        active_patch_target=state.active_patch_target,
        file_outline=file_outline,
        real_anchors=real_anchors,
        file_snippet=file_snippet,
        last_error=truncate_middle(state.last_runtime_error, 1800),
        history=history,
        compact_level=state.prompt_compaction_level,
    )
    prompt = "\n".join(bit for bit in bits if bit is not None)

    limit = max(1500, int(config.PROMPT_CHAR_LIMIT or 12000))
    if len(prompt) <= limit:
        return prompt

    state.prompt_compaction_level += 1
    snippet_lines, _, _ = _prompt_budget(state.prompt_compaction_level)
    if state.mode == "patch" and state.active_patch_target:
        outline_items = _discover_file_outline(state.active_patch_target, state.active_project_root, max_items=12)
        anchor_items = _discover_real_anchors(state.active_patch_target, state.active_project_root, max_items=8)
        file_outline = "\n".join(f"- L{line_no}: {text}" for line_no, text in outline_items[:12])
        real_anchors = "\n".join(f'- "{text}"' for text in anchor_items[:8])
        file_snippet = _focused_patch_snippets(
            state.active_patch_target,
            state.active_project_root,
            user_prompt,
            snippet_lines,
        )
    bits = build_instruction_bits(
        user_prompt,
        mode=state.mode,
        project_root=state.active_project_root,
        target_files=state.target_files,
        active_patch_target=state.active_patch_target,
        file_outline=truncate_middle(file_outline, limit // 5),
        real_anchors=truncate_middle(real_anchors, limit // 6),
        file_snippet=truncate_middle(file_snippet, limit // 2),
        last_error=truncate_middle(state.last_runtime_error, limit // 5),
        history=history[-2:],
        compact_level=state.prompt_compaction_level,
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


def _build_atomic_patch_stage(actions, state):
    if state.mode != "patch":
        return None

    counts = {}
    ordered_paths = []
    for action in actions:
        action_type = str(action.get("type", "")).strip()
        if action_type not in ATOMIC_PATCH_FILE_ACTIONS:
            continue
        rel_path = str(action.get("args", {}).get("path", "")).strip()
        if not rel_path:
            continue
        counts[rel_path] = counts.get(rel_path, 0) + 1
        if rel_path not in ordered_paths:
            ordered_paths.append(rel_path)

    staged_paths = [path for path in ordered_paths if counts.get(path, 0) > 1]
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


def _hard_rescue_handoff(state, history, log, reason):
    if (
        state.current_provider.lower() != "openai"
        and config.OPENAI_RESCUE_ENABLED
        and config.OPENAI_API_KEY.strip()
    ):
        state.current_provider = "openai"
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
    fallback = state.active_patch_target if state.mode == "patch" else "app.py"
    clean = _sanitize_target_token(raw_path or fallback, state.active_project_root, patch_mode=state.mode == "patch")
    if clean:
        return clean

    if state.mode == "patch":
        return state.active_patch_target

    candidate = normalize_rel_path(raw_path or fallback)
    abs_candidate = os.path.abspath(os.path.join(state.active_project_root, candidate))
    if is_subpath(state.active_project_root, abs_candidate):
        return candidate
    return fallback


def _normalize_action(action, state):
    action_type = str(action.get("type", "")).strip()
    args = action.get("args") if isinstance(action.get("args"), dict) else {}

    if action_type == "write_file":
        return {
            "type": action_type,
            "args": {
                "path": _sanitize_action_path(args.get("path"), state),
                "content": str(args.get("content", "")),
            },
        }

    if action_type == "replace_in_file":
        return {
            "type": action_type,
            "args": {
                "path": _sanitize_action_path(args.get("path"), state),
                "old": str(args.get("old", "")),
                "new": str(args.get("new", "")),
            },
        }

    if action_type == "patch_lines":
        return {
            "type": action_type,
            "args": {
                "path": _sanitize_action_path(args.get("path"), state),
                "start_line": args.get("start_line"),
                "end_line": args.get("end_line"),
                "new_content": str(args.get("new_content", args.get("content", ""))),
            },
        }

    if action_type in {"insert_before", "insert_after"}:
        fallback_anchor = args.get("target", "")
        if isinstance(fallback_anchor, int):
            fallback_anchor = ""
        return {
            "type": action_type,
            "args": {
                "path": _sanitize_action_path(args.get("path"), state),
                "anchor": str(args.get("anchor", fallback_anchor)),
                "content": str(args.get("content", args.get("new_content", ""))),
                "line_number": args.get("line_number", args.get("line")),
            },
        }

    if action_type == "replace_block":
        start_fallback = args.get("start", "")
        end_fallback = args.get("end", "")
        if isinstance(start_fallback, int):
            start_fallback = ""
        if isinstance(end_fallback, int):
            end_fallback = ""
        return {
            "type": action_type,
            "args": {
                "path": _sanitize_action_path(args.get("path"), state),
                "start_anchor": str(args.get("start_anchor", start_fallback)),
                "end_anchor": str(args.get("end_anchor", end_fallback)),
                "content": str(args.get("content", args.get("new_content", ""))),
                "start_line": args.get("start_line"),
                "end_line": args.get("end_line"),
            },
        }

    if action_type == "begin_file_rewrite":
        return {
            "type": action_type,
            "args": {
                "path": _sanitize_action_path(args.get("path"), state),
                "expected_parts": args.get("expected_parts", args.get("parts")),
            },
        }

    if action_type == "append_file_chunk":
        return {
            "type": action_type,
            "args": {
                "path": _sanitize_action_path(args.get("path"), state),
                "part": args.get("part", args.get("index")),
                "content": str(args.get("content", args.get("chunk", ""))),
            },
        }

    if action_type == "finalize_file_rewrite":
        return {
            "type": action_type,
            "args": {
                "path": _sanitize_action_path(args.get("path"), state),
            },
        }

    if action_type == "find_in_file":
        return {
            "type": action_type,
            "args": {
                "path": _sanitize_action_path(args.get("path"), state),
                "query": str(args.get("query", "")),
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
                "cmd": normalize_cmd_paths(args.get("cmd", "")),
            },
        }

    return {"type": action_type, "args": args}


def _execute_action(action, state, project_root_override=None):
    action_type = action.get("type")
    args = action.get("args", {})
    project_root = project_root_override or state.active_project_root

    if action_type == "write_file":
        return write_file(
            args.get("path", ""),
            args.get("content", ""),
            project_root=project_root,
            patch_mode=(state.mode == "patch"),
            allow_create=(state.mode != "patch"),
        )

    if action_type == "replace_in_file":
        return replace_in_file(
            args.get("path", ""),
            args.get("old", ""),
            args.get("new", ""),
            project_root=project_root,
        )

    if action_type == "insert_before":
        return insert_before(
            args.get("path", ""),
            args.get("anchor", ""),
            args.get("content", ""),
            project_root=project_root,
            line_number=args.get("line_number"),
        )

    if action_type == "insert_after":
        return insert_after(
            args.get("path", ""),
            args.get("anchor", ""),
            args.get("content", ""),
            project_root=project_root,
            line_number=args.get("line_number"),
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
        )

    from contracts import Observation

    return Observation(False, f"UNKNOWN ACTION: {action_type}", changed=False)


def _format_history_observation(summary, details):
    if details:
        return summary + " | " + truncate_middle(details, 1200)
    return summary


def _plan_indicates_completion(plan):
    lower = _normalize_prompt_text(plan)
    return any(keyword in lower for keyword in PATCH_DONE_KEYWORDS)


def _completion_decision(state, plan, had_run_cmd, touched_paths, verify_observation):
    verified_ok = not touched_paths or bool(verify_observation.ok)
    plan_done = _plan_indicates_completion(plan)
    patch_target_touched = False
    if state.mode == "patch" and state.active_patch_target:
        patch_target_touched = state.active_patch_target in touched_paths

    if state.mode == "create":
        if had_run_cmd and verified_ok:
            return True, "EXECUTION/CHECK PASSED -> STOP"
        if plan_done and verified_ok:
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
    return (
        f"SINGLE-FILE PATCH FALLBACK for {state.active_patch_target or 'app.py'}: "
        "prefer anchored edits (replace_in_file, insert_before/after, replace_block). "
        "If full rewrite is needed for a large file, use begin_file_rewrite + append_file_chunk + finalize_file_rewrite. "
        "Use one write_file only for a small full-file rewrite."
    )


def run(prompt, logger=print, stop_checker=None, model_timeout_handler=None):
    state = _build_state(prompt)
    os.makedirs(state.active_project_root, exist_ok=True)
    ensure_gitignore(state.active_project_root)
    if not git_is_repo(state.active_project_root):
        git_init(state.active_project_root)
    _save_session_state(state)

    log_path = os.path.join(os.getcwd(), config.LOG_FILE)

    with open(log_path, "w", encoding="utf-8") as log_file:
        tee = Tee(sys.stdout, log_file)

        def log(msg=""):
            tee.write(str(msg) + "\n")
            if logger is not None:
                logger(str(msg))

        history = []
        consecutive_parse_errors = 0
        empty_done_retry_used = False

        log(f"MODE: {state.mode}")
        log(f"ACTIVE PROJECT ROOT: {state.active_project_root}")
        if state.mode == "patch":
            log(f"ACTIVE PATCH TARGET: {state.active_patch_target}")

        for iteration in range(config.MAX_ITERATIONS):
            state.iteration_count = iteration + 1
            _save_session_state(state)
            if stop_checker and stop_checker():
                log("STOP REQUESTED")
                break

            if state.mode == "patch":
                _refresh_patch_context(state)

            log(f"\nITER {iteration}")
            prompt_payload = build_prompt(prompt, history, state)
            log(f"PROMPT CHARS: {len(prompt_payload)}")
            try:
                raw = call_model(
                    prompt_payload,
                    provider_override=state.current_provider,
                    max_output_tokens=config.MAX_OUTPUT_TOKENS,
                    timeout_handler=model_timeout_handler,
                    timeout_seconds=config.MODEL_TIMEOUT,
                )
            except Exception as e:
                message = str(e)
                log(message)
                history.append(message)
                if "MODEL REQUEST KILLED BY USER" in message:
                    log("MODEL REQUEST KILLED -> CONTINUE")
                    _save_session_state(state)
                    continue
                if _overflow_like(message):
                    state.prompt_compaction_level += 1
                    log("CONTEXT PRESSURE DETECTED -> COMPACTING PROMPT")
                    _save_session_state(state)
                    continue
                if _hard_rescue_handoff(state, history, log, reason=message):
                    _save_session_state(state)
                    continue
                break

            log("RAW: " + truncate_middle(str(raw), 3000))

            try:
                data = parse_response(
                    raw,
                    active_target=state.active_patch_target if state.mode == "patch" else "",
                    expected_file_count=state.expected_file_count,
                    single_file_task=state.single_file_task,
                )
                consecutive_parse_errors = 0
            except Exception as e:
                message = str(e)
                log(message)
                history.append(message)
                if state.mode == "patch" and state.single_file_task:
                    fallback_note = _single_file_patch_retry_note(state)
                    if fallback_note not in history[-4:]:
                        log("PATCH PARSE FALLBACK -> REQUEST ANCHORED OR CHUNKED RETRY")
                        history.append(fallback_note)
                consecutive_parse_errors += 1
                if consecutive_parse_errors >= config.MAX_PARSE_ERRORS and _hard_rescue_handoff(state, history, log, reason=message):
                    consecutive_parse_errors = 0
                    _save_session_state(state)
                    continue
                if consecutive_parse_errors >= config.MAX_PARSE_ERRORS:
                    log("STOP: repeated parse errors")
                    break
                continue

            actions = [_normalize_action(action, state) for action in data.get("actions", [])]
            plan = str(data.get("plan", ""))
            plan_fingerprint = _fingerprint_plan(plan, actions)

            if plan_fingerprint == state.last_plan_fingerprint:
                state.stuck_iterations += 1
            else:
                state.stuck_iterations = 0
            state.last_plan_fingerprint = plan_fingerprint

            if state.stuck_iterations >= config.STALL_TRIGGER:
                history.append("Repeated same plan without progress. Refresh from current files only.")
                if _hard_rescue_handoff(state, history, log, reason="repeated same plan"):
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
                if config.ALLOW_EMPTY_DONE_RETRY and not empty_done_retry_used and not state.progress_happened:
                    empty_done_retry_used = True
                    msg = "EMPTY DONE TOO EARLY -> RETRY. You did nothing yet."
                    log(msg)
                    history.append(msg)
                    _save_session_state(state)
                    continue
                log("DONE")
                break

            executed_count = 0
            had_run_cmd = False
            touched_paths = []
            iteration_progress = False
            iteration_error = ""
            stage_context = _build_atomic_patch_stage(actions, state)

            try:
                for action in actions:
                    if stop_checker and stop_checker():
                        log("STOP REQUESTED")
                        return log_path

                    action_key = json.dumps(action, ensure_ascii=False, sort_keys=True)
                    repeated_count = state.duplicate_action_cache.get(action_key, 0)
                    if repeated_count >= config.REPEAT_ACTION_LIMIT:
                        msg = f"BLOCK REPEATED ACTION: {action.get('type')}"
                        log(msg)
                        history.append(msg)
                        if state.mode == "patch":
                            _refresh_patch_context(state)
                        state.stuck_iterations += 1
                        continue

                    created_by_action = False
                    if action.get("type") in {"write_file", "finalize_file_rewrite"}:
                        rel_path = action.get("args", {}).get("path", "")
                        abs_path = os.path.abspath(os.path.join(state.active_project_root, rel_path))
                        created_by_action = not os.path.exists(abs_path)

                    action_root = _stage_action_project_root(action, state, stage_context)
                    staged_action = action_root != state.active_project_root
                    observation = _execute_action(action, state, project_root_override=action_root)
                    if staged_action:
                        metadata = dict(observation.metadata or {})
                        metadata["touches_file"] = False
                        metadata["staged"] = True
                        observation.metadata = metadata
                        observation.summary = observation.summary + " [staged]"

                    executed_count += 1
                    log(observation.summary)
                    if observation.details:
                        log(observation.details)
                    state.last_useful_observation_summary = observation.summary

                    history.append(_format_history_observation(observation.summary, observation.details))

                    progress_signal = observation.changed and (
                        observation.tool in PROGRESS_TOOLS
                        or bool((observation.metadata or {}).get("progress"))
                    )
                    if progress_signal:
                        iteration_progress = True
                        state.progress_happened = True
                        state.duplicate_action_cache.clear()
                    else:
                        state.duplicate_action_cache[action_key] = repeated_count + 1

                    if observation.tool == "run_cmd":
                        had_run_cmd = True
                        if not observation.ok:
                            iteration_error = observation.details or observation.summary

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
                            if "anchor not found" in details_lower or "missing anchor" in details_lower:
                                grounded = _grounded_patch_retry_context(target_path, state.active_project_root, prompt)
                                log("GROUNDING RETRY WITH REAL OUTLINE/ANCHORS")
                                history.append("Grounded retry context after missing anchor:\n" + truncate_middle(grounded, 2200))

                    if not observation.ok and not iteration_error:
                        iteration_error = observation.details or observation.summary

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

            if executed_count == 0:
                msg = "NO EXECUTED ACTIONS"
                log(msg)
                history.append(msg)
                if _hard_rescue_handoff(state, history, log, reason=msg):
                    _save_session_state(state)
                    continue
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

            if iteration_error:
                state.last_runtime_error = iteration_error
                log("ITERATION FAILED -> CONTINUE")
                _save_session_state(state)
                continue

            state.last_runtime_error = ""

            if iteration_progress and config.AUTO_GIT_CHECKPOINTS:
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
            )
            if should_stop:
                log(stop_reason)
                _save_session_state(state)
                break

            if iteration_progress:
                log("PROGRESS APPLIED -> CONTINUE")
            else:
                log("NO REAL PROGRESS -> CONTINUE")
            _save_session_state(state)

        log(f"LOG SAVED TO: {log_path}")
        _save_session_state(state)
        return log_path

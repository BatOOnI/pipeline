import ast
import importlib.util
import os
import re
import shlex
import subprocess
import sys

import config
from contracts import Observation
from utils import is_subpath


PATH_LIKE_RE = re.compile(r"[\\/]|^\.\.?$|\.[A-Za-z0-9_+-]{1,8}$")
SHELL_TEXT_EXTENSIONS = {".bat", ".cmd", ".ps1", ".sh"}
WINDOWS_SCRIPT_EXTENSIONS = {".bat", ".cmd", ".ps1"}


def _resolve_path(path, project_root=None):
    raw = str(path or "").strip().strip('"').strip("'")
    if not raw:
        raise ValueError("Missing path")

    if project_root:
        base = os.path.abspath(project_root)
        candidate = raw if os.path.isabs(raw) else os.path.join(base, raw)
        candidate = os.path.abspath(candidate)
        if not is_subpath(base, candidate):
            raise ValueError(f"Path escapes project root: {path}")
        return candidate

    return os.path.abspath(raw)


def _rel_path(path, project_root=None):
    if project_root:
        try:
            return os.path.relpath(path, project_root)
        except Exception:
            pass
    return path


def _read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_text(path, content):
    extension = os.path.splitext(path)[1].lower()
    if extension in WINDOWS_SCRIPT_EXTENSIONS:
        content = content.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)


def _normalize_shell_script_content(path, content):
    extension = os.path.splitext(path)[1].lower()
    if extension not in SHELL_TEXT_EXTENSIONS or not isinstance(content, str):
        return content, ""

    if not any(marker in content for marker in ("\\r\\n", "\\n", "\\t")):
        return content, ""

    actual_newlines = content.count("\n")
    escaped_newlines = content.count("\\n") + content.count("\\r\\n")
    if actual_newlines > 0 and escaped_newlines <= actual_newlines:
        return content, ""

    normalized = content.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
    shell_markers = (
        "@echo off",
        "echo ",
        "cd ",
        "set ",
        "call ",
        "py -3",
        "python ",
        "pause",
        "#!/bin/",
        "powershell",
        "pip install",
    )
    lowered = normalized.lower()
    if not any(marker in lowered for marker in (item.lower() for item in shell_markers)):
        return content, ""

    return normalized, "Normalized escaped newlines/tabs in shell script content."


def _shell_write_warning(path, content):
    extension = os.path.splitext(path)[1].lower()
    if extension not in {".bat", ".cmd"}:
        return ""
    if "\\n" in content or "\\r\\n" in content:
        return "WARNING: batch file still contains literal \\n sequences."
    return ""


def _looks_like_full_file_rewrite(old_content, new_content):
    old_lines = [line for line in (old_content or "").splitlines() if line.strip()]
    new_lines = [line for line in (new_content or "").splitlines() if line.strip()]

    if not old_lines:
        return True
    if len(old_lines) <= 3 and len(new_lines) >= 1:
        return True
    if len(new_lines) < 3:
        return False

    ratio = len(new_lines) / max(1, len(old_lines))
    if ratio >= config.PATCH_WRITE_MIN_RATIO:
        return True

    anchors = 0
    for line in old_lines[:60]:
        stripped = line.strip()
        if len(stripped) < 8:
            continue
        if stripped in new_content:
            anchors += 1
        if anchors >= 2:
            return True

    return False


def read_file_snippet(path, project_root=None, max_lines=60, around_line=None):
    try:
        abs_path = _resolve_path(path, project_root=project_root)
        content = _read_file(abs_path)
    except Exception as e:
        return f"SNIPPET_ERROR: {e}"

    lines = content.splitlines()
    if not lines:
        return "0001: "

    max_lines = max(5, int(max_lines or 60))
    if around_line:
        center = max(1, int(around_line))
        half = max_lines // 2
        start = max(0, center - half - 1)
        end = min(len(lines), start + max_lines)
    else:
        start = 0
        end = min(len(lines), max_lines)

    snippet = [f"{index + 1:04d}: {lines[index]}" for index in range(start, end)]
    if end < len(lines):
        snippet.append("....: ... truncated ...")
    return "\n".join(snippet)


def write_file(path, content, project_root=None, patch_mode=False, allow_create=True):
    try:
        abs_path = _resolve_path(path, project_root=project_root)
    except Exception as e:
        return Observation(False, f"WRITE REJECTED {path}", changed=False, details=str(e), tool="write_file", path=str(path))

    existed = os.path.exists(abs_path)
    if patch_mode and not existed and not allow_create:
        return Observation(
            False,
            f"PATCH WRITE BLOCKED {_rel_path(abs_path, project_root)}",
            changed=False,
            details="Patch mode cannot create new files unless explicitly requested.",
            tool="write_file",
            path=abs_path,
        )

    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    content, normalization_note = _normalize_shell_script_content(abs_path, content)
    old_content = _read_file(abs_path) if existed else ""
    if existed and old_content == content:
        return Observation(True, f"SKIP SAME FILE {_rel_path(abs_path, project_root)}", changed=False, tool="write_file", path=abs_path)

    if patch_mode and existed and not _looks_like_full_file_rewrite(old_content, content):
        return Observation(
            False,
            f"PATCH WRITE REJECTED {_rel_path(abs_path, project_root)}",
            changed=False,
            details="write_file in patch mode requires the full corrected file, not a tiny fragment.",
            tool="write_file",
            path=abs_path,
        )

    _write_text(abs_path, content)
    written_content = _read_file(abs_path)
    details = normalization_note
    warning = _shell_write_warning(abs_path, written_content)
    if warning:
        details = (details + "\n" if details else "") + warning
    return Observation(
        True,
        f"Wrote {_rel_path(abs_path, project_root)}",
        changed=True,
        details=details,
        tool="write_file",
        path=abs_path,
    )


def find_in_file(path, query, project_root=None, context_lines=3):
    try:
        abs_path = _resolve_path(path, project_root=project_root)
        content = _read_file(abs_path)
    except Exception as e:
        return Observation(False, f"find_in_file failed {path}", changed=False, details=str(e), tool="find_in_file", path=str(path))

    query = str(query or "")
    if not query:
        return Observation(False, f"find_in_file failed {_rel_path(abs_path, project_root)}", changed=False, details="Missing query", tool="find_in_file", path=abs_path)

    lines = content.splitlines()
    lowered = query.lower()
    hits = []
    for index, line in enumerate(lines):
        if query in line or lowered in line.lower():
            start = max(0, index - max(0, int(context_lines)))
            end = min(len(lines), index + max(0, int(context_lines)) + 1)
            block = [f"{i + 1:04d}: {lines[i]}" for i in range(start, end)]
            hits.append("\n".join(block))
        if len(hits) >= 3:
            break

    if not hits:
        return Observation(True, f"find_in_file no match {_rel_path(abs_path, project_root)}", changed=False, tool="find_in_file", path=abs_path)

    return Observation(
        True,
        f"find_in_file matched {_rel_path(abs_path, project_root)}",
        changed=False,
        details="\n\n".join(hits),
        tool="find_in_file",
        path=abs_path,
    )


def replace_in_file(path, old, new, project_root=None):
    try:
        abs_path = _resolve_path(path, project_root=project_root)
        content = _read_file(abs_path)
    except Exception as e:
        return Observation(False, f"replace_in_file failed {path}", changed=False, details=str(e), tool="replace_in_file", path=str(path))

    old = str(old or "")
    new = str(new or "")
    if not old:
        return Observation(False, f"replace_in_file failed {_rel_path(abs_path, project_root)}", changed=False, details="Missing 'old' content", tool="replace_in_file", path=abs_path)

    occurrences = content.count(old)
    if occurrences == 0:
        return Observation(
            False,
            f"replace_in_file miss {_rel_path(abs_path, project_root)}",
            changed=False,
            details="Old block was not found in current file snapshot.",
            tool="replace_in_file",
            path=abs_path,
        )
    if occurrences > 1:
        return Observation(
            False,
            f"replace_in_file ambiguous {_rel_path(abs_path, project_root)}",
            changed=False,
            details=f"Old block matched {occurrences} places. Use a more specific block or patch_lines.",
            tool="replace_in_file",
            path=abs_path,
        )

    updated = content.replace(old, new, 1)
    if updated == content:
        return Observation(True, f"replace_in_file no-op {_rel_path(abs_path, project_root)}", changed=False, tool="replace_in_file", path=abs_path)

    _write_text(abs_path, updated)
    return Observation(True, f"replace_in_file updated {_rel_path(abs_path, project_root)}", changed=True, tool="replace_in_file", path=abs_path)


def patch_lines(path, start_line, end_line, new_content, project_root=None):
    try:
        abs_path = _resolve_path(path, project_root=project_root)
        content = _read_file(abs_path)
    except Exception as e:
        return Observation(False, f"patch_lines failed {path}", changed=False, details=str(e), tool="patch_lines", path=str(path))

    try:
        start_line = int(start_line)
        end_line = int(end_line)
    except Exception:
        return Observation(False, f"patch_lines failed {_rel_path(abs_path, project_root)}", changed=False, details="Line numbers must be integers", tool="patch_lines", path=abs_path)

    lines = content.splitlines()
    if start_line < 1 or end_line < start_line or end_line > len(lines):
        return Observation(
            False,
            f"patch_lines range error {_rel_path(abs_path, project_root)}",
            changed=False,
            details=f"Valid line range is 1..{len(lines)}",
            tool="patch_lines",
            path=abs_path,
        )

    replacement = str(new_content or "").splitlines()
    updated_lines = lines[: start_line - 1] + replacement + lines[end_line:]
    updated = "\n".join(updated_lines)
    if content.endswith("\n"):
        updated += "\n"

    if updated == content:
        return Observation(True, f"patch_lines no-op {_rel_path(abs_path, project_root)}", changed=False, tool="patch_lines", path=abs_path)

    _write_text(abs_path, updated)
    return Observation(
        True,
        f"patch_lines updated {_rel_path(abs_path, project_root)}:{start_line}-{end_line}",
        changed=True,
        tool="patch_lines",
        path=abs_path,
    )


def mkdir(path, project_root=None, patch_mode=False):
    if patch_mode:
        return Observation(
            False,
            f"mkdir blocked {path}",
            changed=False,
            details="Patch mode cannot create new folders unless explicitly requested.",
            tool="mkdir",
            path=str(path),
        )

    try:
        abs_path = _resolve_path(path, project_root=project_root)
        existed = os.path.isdir(abs_path)
        os.makedirs(abs_path, exist_ok=True)
    except Exception as e:
        return Observation(False, f"mkdir failed {path}", changed=False, details=str(e), tool="mkdir", path=str(path))

    return Observation(True, f"mkdir {_rel_path(abs_path, project_root)}", changed=not existed, tool="mkdir", path=abs_path)


def normalize_cmd_platform(cmd):
    if isinstance(cmd, list):
        fixed = []
        for item in cmd:
            item = str(item)
            if item == "python3":
                item = "python"
            fixed.append(item)
        return fixed

    if isinstance(cmd, str):
        return cmd.replace("python3", "python")

    return cmd


def _is_path_like(arg):
    return bool(PATH_LIKE_RE.search(str(arg or "")))


def _validate_cmd_paths(args, project_root):
    if not project_root:
        return True, ""

    root = os.path.abspath(project_root)
    for arg in args[1:]:
        text = str(arg)
        if text.startswith("-"):
            continue
        if not _is_path_like(text):
            continue
        try:
            candidate = _resolve_path(text, project_root=root)
        except Exception as e:
            return False, str(e)
        if not is_subpath(root, candidate):
            return False, f"Command path escapes project root: {text}"
    return True, ""


def _prepare_cmd(cmd, project_root=None):
    cmd = normalize_cmd_platform(cmd)
    if isinstance(cmd, str):
        if any(token in cmd for token in ("&&", "||", ";", "|", ">", "<")):
            return None, "Shell operators are not allowed in run_cmd"
        try:
            cmd = shlex.split(cmd, posix=False)
        except Exception as e:
            return None, f"Could not parse command: {e}"

    if not isinstance(cmd, list) or not cmd:
        return None, "run_cmd requires a non-empty command"

    cmd = [str(part) for part in cmd]
    ok, error = _validate_cmd_paths(cmd, project_root=project_root)
    if not ok:
        return None, error

    return cmd, ""


def run_cmd(cmd, cwd=None, project_root=None):
    project_root = project_root or cwd
    prepared, error = _prepare_cmd(cmd, project_root=project_root)
    if error:
        return Observation(False, f"CMD BLOCKED: {cmd}", changed=False, details=error, tool="run_cmd")

    try:
        result = subprocess.run(
            prepared,
            shell=False,
            capture_output=True,
            text=True,
            cwd=cwd,
            input="",
            timeout=config.RUN_TIMEOUT,
        )
        details = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()[:4000]
        return Observation(
            result.returncode == 0,
            f"CMD exit={result.returncode}: {prepared}",
            changed=False,
            details=details,
            tool="run_cmd",
            metadata={"returncode": result.returncode},
        )
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        err = e.stderr or ""
        return Observation(
            False,
            f"CMD TIMEOUT: {prepared}",
            changed=False,
            details=((str(out) + "\n" + str(err)).strip())[:4000],
            tool="run_cmd",
        )
    except Exception as e:
        return Observation(False, f"CMD FAILED: {prepared}", changed=False, details=str(e), tool="run_cmd")


def _module_exists_locally(module_name, project_root, current_dir=None):
    candidates = []
    if current_dir:
        candidates.append(os.path.join(current_dir, module_name + ".py"))
        candidates.append(os.path.join(current_dir, module_name, "__init__.py"))
    if project_root:
        candidates.append(os.path.join(project_root, module_name + ".py"))
        candidates.append(os.path.join(project_root, module_name, "__init__.py"))
    return any(os.path.exists(candidate) for candidate in candidates)


def check_python_module(module_name, project_root=None, current_dir=None):
    stdlib_names = getattr(sys, "stdlib_module_names", set())
    if module_name in sys.builtin_module_names or module_name in stdlib_names:
        return True, "stdlib"

    if _module_exists_locally(module_name, project_root=project_root, current_dir=current_dir):
        return True, "local"

    spec = importlib.util.find_spec(module_name)
    if spec is not None:
        return True, "installed"

    return False, "missing"


def scan_python_dependencies(path, project_root=None):
    abs_path = _resolve_path(path, project_root=project_root)
    content = _read_file(abs_path)
    try:
        tree = ast.parse(content, filename=abs_path)
    except SyntaxError as e:
        return [f"SyntaxError while scanning imports: {e}"]

    current_dir = os.path.dirname(abs_path)
    missing = []
    seen = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name.split(".")[0]
                if name in seen:
                    continue
                ok, _ = check_python_module(name, project_root=project_root, current_dir=current_dir)
                if not ok:
                    missing.append(name)
                seen.add(name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and not node.module:
                continue
            if node.module:
                name = node.module.split(".")[0]
                if name in seen:
                    continue
                ok, _ = check_python_module(name, project_root=project_root, current_dir=current_dir)
                if not ok:
                    missing.append(name)
                seen.add(name)

    return missing


def verify_python_file(path, project_root=None, smoke_run=False):
    abs_path = _resolve_path(path, project_root=project_root)
    rel_path = _rel_path(abs_path, project_root)
    cwd = project_root or os.path.dirname(abs_path)

    compile_result = subprocess.run(
        [sys.executable, "-m", "py_compile", abs_path],
        shell=False,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=max(5, config.RUN_TIMEOUT),
    )
    if compile_result.returncode != 0:
        details = ((compile_result.stdout or "") + "\n" + (compile_result.stderr or "")).strip()[:4000]
        return Observation(False, f"VERIFY FAIL {rel_path}", changed=False, details=details, tool="verify_python", path=abs_path)

    missing = scan_python_dependencies(abs_path, project_root=project_root)
    if missing:
        detail = "Missing imports: " + ", ".join(sorted(set(missing)))
        return Observation(False, f"VERIFY FAIL {rel_path}", changed=False, details=detail, tool="verify_python", path=abs_path)

    if smoke_run:
        result = subprocess.run(
            [sys.executable, abs_path],
            shell=False,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=min(max(5, config.RUN_TIMEOUT), 8),
        )
        if result.returncode != 0:
            details = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()[:4000]
            return Observation(False, f"SMOKE FAIL {rel_path}", changed=False, details=details, tool="verify_python", path=abs_path)

    detail = "py_compile ok; dependency scan ok"
    if smoke_run:
        detail += "; smoke run ok"
    return Observation(True, f"VERIFY OK {rel_path}", changed=False, details=detail, tool="verify_python", path=abs_path)


def verify_touched_paths(paths, project_root=None, smoke_run=False):
    checked = []
    for path in paths:
        extension = os.path.splitext(str(path))[1].lower()
        if extension != ".py":
            continue
        observation = verify_python_file(path, project_root=project_root, smoke_run=smoke_run and not checked)
        if not observation.ok:
            return observation
        checked.append(os.path.basename(str(path)))

    if not checked:
        return Observation(True, "VERIFY SKIP", changed=False, details="No Python files changed", tool="verify_python")

    return Observation(True, f"VERIFY OK: {', '.join(checked)}", changed=False, details="All touched Python files compiled cleanly", tool="verify_python")

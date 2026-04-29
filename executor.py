import ast
import base64
import binascii
import hashlib
import importlib.util
import json
import os
import re
import shlex
import subprocess
import time
import sys
import tempfile
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import config
from contracts import Observation
from utils import is_subpath


PATH_LIKE_RE = re.compile(r"[\\/]|^\.\.?$|\.[A-Za-z0-9_+-]{1,8}$")
WINDOWS_SIMPLE_LAUNCH_RE = re.compile(r'^\s*cd\s+/d\s+(".*?"|\S+)\s*&&\s*(.+?)\s*$', re.IGNORECASE)
SHELL_TEXT_EXTENSIONS = {".bat", ".cmd", ".ps1", ".sh"}
WINDOWS_SCRIPT_EXTENSIONS = {".bat", ".cmd", ".ps1"}
REWRITE_STATE_DIR = os.path.join(".agent", "rewrite_state")


def _decode_b64_text(value, field_name):
    if value is None:
        return None, ""
    if not isinstance(value, str):
        return None, f"{field_name} must be base64 string."
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        return None, f"Invalid base64 in {field_name}: {exc}"
    try:
        return decoded.decode("utf-8"), ""
    except UnicodeDecodeError as exc:
        return None, f"{field_name} is not valid UTF-8 text: {exc}"


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


def _network_allowed():
    return bool(getattr(config, "NETWORK_ENABLED", True))


def _parse_allowed_hosts():
    raw = str(getattr(config, "NETWORK_ALLOWED_HOSTS", "") or "")
    if not raw.strip():
        return []
    parts = []
    for token in re.split(r"[,;\n]", raw):
        item = str(token or "").strip().lower()
        if item:
            parts.append(item.lstrip("."))
    return parts


def _validate_http_url(url):
    text = str(url or "").strip()
    if not text:
        return "", "Missing url."
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"}:
        return "", "Only http/https URLs are allowed."
    if not parsed.netloc:
        return "", "URL must include host."
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return "", "URL host is missing."
    allowed_hosts = _parse_allowed_hosts()
    if allowed_hosts:
        host_ok = any(host == allowed or host.endswith("." + allowed) for allowed in allowed_hosts)
        if not host_ok:
            return "", f"Host '{host}' is not allowed by NETWORK_ALLOWED_HOSTS."
    return text, ""


def http_get(url, max_chars=None, output_path=None, project_root=None):
    if not _network_allowed():
        return Observation(
            False,
            "HTTP BLOCKED",
            changed=False,
            details="Network access is disabled by configuration (NETWORK_ENABLED=OFF).",
            tool="http_get",
        )
    normalized_url, err = _validate_http_url(url)
    if err:
        return Observation(False, "HTTP BLOCKED", changed=False, details=err, tool="http_get")
    output_text = str(output_path or "").strip()
    if not output_text:
        return Observation(
            False,
            "HTTP GET FAILED",
            changed=False,
            details="http_get requires url and output_path",
            tool="http_get",
        )
    try:
        abs_output_path = _resolve_path(output_text, project_root=project_root)
    except Exception as exc:
        return Observation(
            False,
            "HTTP GET FAILED",
            changed=False,
            details=f"http_get output_path invalid: {exc}",
            tool="http_get",
        )

    try:
        byte_limit = max(256, int(getattr(config, "HTTP_GET_MAX_BYTES", 1000000) or 1000000))
    except Exception:
        byte_limit = 1000000
    try:
        char_limit = max(200, int(max_chars or 4000))
    except Exception:
        char_limit = 4000

    req = Request(normalized_url, headers={"User-Agent": "pipeline-agent/1.0"})
    try:
        with urlopen(req, timeout=max(3, int(config.RUN_TIMEOUT or 15))) as response:
            status = int(getattr(response, "status", 200) or 200)
            content_type = str(response.headers.get("Content-Type", "") or "")
            data = response.read(byte_limit + 1)
            truncated = len(data) > byte_limit
            if truncated:
                data = data[:byte_limit]
    except Exception as exc:
        return Observation(
            False,
            f"HTTP GET FAILED: {normalized_url}",
            changed=False,
            details=str(exc),
            tool="http_get",
        )

    parent = os.path.dirname(abs_output_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        with open(abs_output_path, "wb") as out:
            out.write(data)
    except Exception as exc:
        return Observation(
            False,
            "HTTP GET FAILED",
            changed=False,
            details=f"Could not save output_path: {exc}",
            tool="http_get",
        )

    text = data.decode("utf-8", errors="replace")
    preview = text[:char_limit]
    details = (
        f"url={normalized_url}\nstatus={status}\ncontent_type={content_type}\n"
        f"bytes={len(data)}{' (truncated)' if truncated else ''}\noutput_path={abs_output_path}\n\n{preview}"
    )
    return Observation(
        True,
        f"HTTP GET OK: {normalized_url}",
        changed=True,
        details=details,
        tool="http_get",
        path=abs_output_path,
        metadata={"status": status, "bytes": len(data), "truncated": truncated, "touches_file": True},
    )


def download_file(url, path, project_root=None):
    if not _network_allowed():
        return Observation(
            False,
            "DOWNLOAD BLOCKED",
            changed=False,
            details="Network access is disabled by configuration (NETWORK_ENABLED=OFF).",
            tool="download_file",
        )
    normalized_url, err = _validate_http_url(url)
    if err:
        return Observation(False, "DOWNLOAD BLOCKED", changed=False, details=err, tool="download_file")
    try:
        abs_path = _resolve_path(path, project_root=project_root)
    except Exception as exc:
        return Observation(False, f"DOWNLOAD FAILED: {path}", changed=False, details=str(exc), tool="download_file", path=str(path))

    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        max_bytes = max(1024, int(getattr(config, "DOWNLOAD_MAX_BYTES", 15000000) or 15000000))
    except Exception:
        max_bytes = 15000000

    req = Request(normalized_url, headers={"User-Agent": "pipeline-agent/1.0"})
    total = 0
    status = 0
    try:
        with urlopen(req, timeout=max(3, int(config.RUN_TIMEOUT or 15))) as response, open(abs_path, "wb") as out:
            status = int(getattr(response, "status", 200) or 200)
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    out.close()
                    try:
                        os.remove(abs_path)
                    except Exception:
                        pass
                    return Observation(
                        False,
                        f"DOWNLOAD BLOCKED: {_rel_path(abs_path, project_root)}",
                        changed=False,
                        details=f"Download exceeds limit ({max_bytes} bytes).",
                        tool="download_file",
                        path=abs_path,
                    )
                out.write(chunk)
    except Exception as exc:
        try:
            if os.path.exists(abs_path):
                os.remove(abs_path)
        except Exception:
            pass
        return Observation(
            False,
            f"DOWNLOAD FAILED: {_rel_path(abs_path, project_root)}",
            changed=False,
            details=str(exc),
            tool="download_file",
            path=abs_path,
        )

    rel = _rel_path(abs_path, project_root)
    return Observation(
        True,
        f"Downloaded {rel}",
        changed=True,
        details=f"url={normalized_url}\nstatus={status}\nbytes={total}",
        tool="download_file",
        path=abs_path,
        metadata={"status": status, "bytes": total, "touches_file": True},
    )


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


def _line_boundaries(content):
    positions = []
    index = 0
    for line in content.splitlines(keepends=True):
        start = index
        index += len(line)
        positions.append((start, index, line))
    if not positions and content == "":
        return []
    if content and (not positions or index < len(content)):
        positions.append((index, len(content), content[index:]))
    return positions


def _resolve_legacy_line_reference(content, line_number, label):
    try:
        line_number = int(line_number)
    except Exception:
        return None, None, None, f"{label} must be an integer."

    boundaries = _line_boundaries(content)
    if not boundaries:
        return None, None, None, f"Cannot resolve {label}: file is empty."
    if line_number < 1 or line_number > len(boundaries):
        return None, None, None, f"Invalid {label}: valid range is 1..{len(boundaries)}."

    start, end, line_text = boundaries[line_number - 1]
    return line_text, start, end, ""


def _rewrite_state_base(project_root):
    root = os.path.abspath(project_root or os.getcwd())
    base = os.path.join(root, REWRITE_STATE_DIR)
    os.makedirs(base, exist_ok=True)
    return base


def _rewrite_state_path(abs_path, project_root):
    key = hashlib.sha256(os.path.normcase(abs_path).encode("utf-8")).hexdigest()
    return os.path.join(_rewrite_state_base(project_root), f"{key}.json")


def _load_rewrite_state(abs_path, project_root):
    state_path = _rewrite_state_path(abs_path, project_root)
    if not os.path.exists(state_path):
        return None
    try:
        with open(state_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _save_rewrite_state(abs_path, project_root, state):
    state_path = _rewrite_state_path(abs_path, project_root)
    with open(state_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False)


def _clear_rewrite_state(abs_path, project_root):
    state_path = _rewrite_state_path(abs_path, project_root)
    try:
        os.remove(state_path)
    except FileNotFoundError:
        pass


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


def read_file(
    path,
    project_root=None,
    max_chars=18000,
    section_id=None,
    line_start=None,
    line_end=None,
    around_anchor=None,
    query=None,
):
    try:
        abs_path = _resolve_path(path, project_root=project_root)
        content = _read_file(abs_path)
    except Exception as e:
        return Observation(False, f"read_file failed {path}", changed=False, details=str(e), tool="read_file", path=str(path))

    lines = content.splitlines()
    total_lines = len(lines)

    def _clip_range(start_line, end_line):
        start = max(1, int(start_line or 1))
        end = max(start, int(end_line or total_lines))
        start = min(start, max(1, total_lines))
        end = min(end, max(1, total_lines))
        if total_lines <= 0:
            return ""
        chunk = [f"{idx:04d}: {lines[idx - 1]}" for idx in range(start, end + 1)]
        return "\n".join(chunk)

    if section_id:
        sec_text = str(section_id).strip().upper()
        if sec_text.startswith("S"):
            sec_text = sec_text[1:]
        try:
            sec_num = int(sec_text)
        except Exception:
            return Observation(
                False,
                f"read_file failed {_rel_path(abs_path, project_root)}",
                changed=False,
                details=f"Invalid section_id: {section_id}",
                tool="read_file",
                path=abs_path,
            )
        section_size = 280
        start = (max(1, sec_num) - 1) * section_size + 1
        end = min(total_lines, start + section_size - 1)
        details = _clip_range(start, end)
        # Regression guard: if section output is placeholder-only while file is not, treat as transport corruption.
        stripped = [line.strip() for line in details.splitlines() if line.strip()]
        section_like = [line for line in stripped if re.fullmatch(r"\d{4}:\s*SECTION", line, re.IGNORECASE)]
        if stripped and len(section_like) >= max(6, int(len(stripped) * 0.8)):
            file_has_literal_section = any(raw.strip().upper() == "SECTION" for raw in lines)
            if not file_has_literal_section:
                return Observation(
                    False,
                    f"read_file corruption {_rel_path(abs_path, project_root)}",
                    changed=False,
                    details="Large-file section read appears placeholder-only (SECTION) but source file does not contain literal SECTION lines.",
                    tool="read_file",
                    path=abs_path,
                )
        return Observation(
            True,
            f"read_file section S{sec_num} {_rel_path(abs_path, project_root)} L{start}-L{end}",
            changed=False,
            details=details,
            tool="read_file",
            path=abs_path,
        )

    line_start_norm = line_start
    line_end_norm = line_end
    if isinstance(line_start_norm, str):
        line_start_norm = line_start_norm.strip()
        if not line_start_norm:
            line_start_norm = None
    if isinstance(line_end_norm, str):
        line_end_norm = line_end_norm.strip()
        if not line_end_norm:
            line_end_norm = None

    if isinstance(line_start_norm, str) and not line_end_norm and re.match(r"^\s*L?\d+\s*-\s*L?\d+\s*$", line_start_norm, re.IGNORECASE):
        tokens = re.findall(r"\d+", line_start_norm)
        if len(tokens) == 2:
            line_start_norm, line_end_norm = int(tokens[0]), int(tokens[1])

    if line_start_norm is not None or line_end_norm is not None:
        try:
            start = int(line_start_norm or 1)
            end = int(line_end_norm or start)
        except Exception:
            return Observation(
                False,
                f"read_file failed {_rel_path(abs_path, project_root)}",
                changed=False,
                details="line_start/line_end must be integers",
                tool="read_file",
                path=abs_path,
            )
        details = _clip_range(start, end)
        return Observation(
            True,
            f"read_file lines {_rel_path(abs_path, project_root)} L{start}-L{end}",
            changed=False,
            details=details,
            tool="read_file",
            path=abs_path,
        )

    if around_anchor or query:
        needle = str(around_anchor or query or "").strip()
        lowered = content.lower()
        pos = lowered.find(needle.lower()) if needle else -1
        if pos < 0:
            return Observation(
                False,
                f"read_file anchor miss {_rel_path(abs_path, project_root)}",
                changed=False,
                details=f'Anchor/query not found: "{needle}"',
                tool="read_file",
                path=abs_path,
            )
        line_no = content[:pos].count("\n") + 1
        half = 40
        start = max(1, line_no - half)
        end = min(max(1, total_lines), line_no + half)
        details = _clip_range(start, end)
        return Observation(
            True,
            f"read_file around anchor {_rel_path(abs_path, project_root)} L{start}-L{end}",
            changed=False,
            details=details,
            tool="read_file",
            path=abs_path,
        )

    max_chars = max(1000, int(max_chars or 18000))
    details = content
    if len(content) > max_chars:
        details = content[:max_chars] + "\n... [truncated] ..."
    return Observation(
        True,
        f"read_file {_rel_path(abs_path, project_root)} chars={len(content)}",
        changed=False,
        details=details,
        tool="read_file",
        path=abs_path,
    )


def write_file(path, content, project_root=None, patch_mode=False, allow_create=True, content_b64=None):
    try:
        abs_path = _resolve_path(path, project_root=project_root)
    except Exception as e:
        return Observation(False, f"WRITE REJECTED {path}", changed=False, details=str(e), tool="write_file", path=str(path))

    decoded, decode_error = _decode_b64_text(content_b64, "content_b64")
    if decode_error:
        return Observation(False, f"WRITE REJECTED {_rel_path(abs_path, project_root)}", changed=False, details=decode_error, tool="write_file", path=abs_path)
    if decoded is not None:
        content = decoded

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
        if os.path.isdir(abs_path):
            return Observation(
                False,
                f"find_in_file failed {_rel_path(abs_path, project_root)}",
                changed=False,
                details="find_in_file requires a file path, got directory. Use list_files or dir first.",
                tool="find_in_file",
                path=abs_path,
            )
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


def replace_in_file(path, old, new, project_root=None, old_b64=None, new_b64=None):
    try:
        abs_path = _resolve_path(path, project_root=project_root)
        content = _read_file(abs_path)
    except Exception as e:
        return Observation(False, f"replace_in_file failed {path}", changed=False, details=str(e), tool="replace_in_file", path=str(path))

    decoded_old, old_error = _decode_b64_text(old_b64, "old_b64")
    if old_error:
        return Observation(False, f"replace_in_file failed {_rel_path(abs_path, project_root)}", changed=False, details=old_error, tool="replace_in_file", path=abs_path)
    decoded_new, new_error = _decode_b64_text(new_b64, "new_b64")
    if new_error:
        return Observation(False, f"replace_in_file failed {_rel_path(abs_path, project_root)}", changed=False, details=new_error, tool="replace_in_file", path=abs_path)
    if decoded_old is not None:
        old = decoded_old
    if decoded_new is not None:
        new = decoded_new

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


def insert_before(path, anchor, content, project_root=None, line_number=None, content_b64=None):
    try:
        abs_path = _resolve_path(path, project_root=project_root)
        current = _read_file(abs_path)
    except Exception as e:
        return Observation(False, f"insert_before failed {path}", changed=False, details=str(e), tool="insert_before", path=str(path))

    decoded_content, decode_error = _decode_b64_text(content_b64, "content_b64")
    if decode_error:
        return Observation(False, f"insert_before failed {_rel_path(abs_path, project_root)}", changed=False, details=decode_error, tool="insert_before", path=abs_path)
    if decoded_content is not None:
        content = decoded_content

    anchor = str(anchor or "")
    insert_text = str(content or "")
    index = None
    if anchor:
        index = current.find(anchor)
        if index < 0:
            return Observation(
                False,
                f"insert_before miss {_rel_path(abs_path, project_root)}",
                changed=False,
                details="Anchor not found in file.",
                tool="insert_before",
                path=abs_path,
            )
    elif line_number is not None:
        anchor, index, _, error = _resolve_legacy_line_reference(current, line_number, "line_number")
        if error:
            return Observation(
                False,
                f"insert_before failed {_rel_path(abs_path, project_root)}",
                changed=False,
                details=error,
                tool="insert_before",
                path=abs_path,
            )
    else:
        return Observation(False, f"insert_before failed {_rel_path(abs_path, project_root)}", changed=False, details="Missing anchor", tool="insert_before", path=abs_path)

    updated = current[:index] + insert_text + current[index:]
    if updated == current:
        return Observation(True, f"insert_before no-op {_rel_path(abs_path, project_root)}", changed=False, tool="insert_before", path=abs_path)

    _write_text(abs_path, updated)
    return Observation(True, f"insert_before updated {_rel_path(abs_path, project_root)}", changed=True, tool="insert_before", path=abs_path)


def insert_after(path, anchor, content, project_root=None, line_number=None, content_b64=None):
    try:
        abs_path = _resolve_path(path, project_root=project_root)
        current = _read_file(abs_path)
    except Exception as e:
        return Observation(False, f"insert_after failed {path}", changed=False, details=str(e), tool="insert_after", path=str(path))

    decoded_content, decode_error = _decode_b64_text(content_b64, "content_b64")
    if decode_error:
        return Observation(False, f"insert_after failed {_rel_path(abs_path, project_root)}", changed=False, details=decode_error, tool="insert_after", path=abs_path)
    if decoded_content is not None:
        content = decoded_content

    anchor = str(anchor or "")
    insert_text = str(content or "")
    insert_at = None
    if anchor:
        index = current.find(anchor)
        if index < 0:
            return Observation(
                False,
                f"insert_after miss {_rel_path(abs_path, project_root)}",
                changed=False,
                details="Anchor not found in file.",
                tool="insert_after",
                path=abs_path,
            )
        insert_at = index + len(anchor)
    elif line_number is not None:
        anchor, _, insert_at, error = _resolve_legacy_line_reference(current, line_number, "line_number")
        if error:
            return Observation(
                False,
                f"insert_after failed {_rel_path(abs_path, project_root)}",
                changed=False,
                details=error,
                tool="insert_after",
                path=abs_path,
            )
    else:
        return Observation(False, f"insert_after failed {_rel_path(abs_path, project_root)}", changed=False, details="Missing anchor", tool="insert_after", path=abs_path)

    updated = current[:insert_at] + insert_text + current[insert_at:]
    if updated == current:
        return Observation(True, f"insert_after no-op {_rel_path(abs_path, project_root)}", changed=False, tool="insert_after", path=abs_path)

    _write_text(abs_path, updated)
    return Observation(True, f"insert_after updated {_rel_path(abs_path, project_root)}", changed=True, tool="insert_after", path=abs_path)


def replace_block(
    path,
    start_anchor,
    end_anchor,
    content,
    project_root=None,
    start_line=None,
    end_line=None,
    new_content=None,
    content_b64=None,
):
    try:
        abs_path = _resolve_path(path, project_root=project_root)
        current = _read_file(abs_path)
    except Exception as e:
        return Observation(False, f"replace_block failed {path}", changed=False, details=str(e), tool="replace_block", path=str(path))

    decoded_content, decode_error = _decode_b64_text(content_b64, "content_b64")
    if decode_error:
        return Observation(False, f"replace_block failed {_rel_path(abs_path, project_root)}", changed=False, details=decode_error, tool="replace_block", path=abs_path)
    if decoded_content is not None:
        content = decoded_content

    start_anchor = str(start_anchor or "")
    end_anchor = str(end_anchor or "")
    replacement = str(content or new_content or "")
    start_index = None
    end_index = None

    if start_anchor:
        start_index = current.find(start_anchor)
        if start_index < 0:
            return Observation(
                False,
                f"replace_block miss {_rel_path(abs_path, project_root)}",
                changed=False,
                details="start_anchor not found.",
                tool="replace_block",
                path=abs_path,
            )
    elif start_line is not None:
        start_anchor, start_index, _, error = _resolve_legacy_line_reference(current, start_line, "start_line")
        if error:
            return Observation(
                False,
                f"replace_block failed {_rel_path(abs_path, project_root)}",
                changed=False,
                details=error,
                tool="replace_block",
                path=abs_path,
            )
    else:
        return Observation(
            False,
            f"replace_block failed {_rel_path(abs_path, project_root)}",
            changed=False,
            details="Missing start_anchor or end_anchor.",
            tool="replace_block",
            path=abs_path,
        )

    if end_anchor:
        end_index = current.find(end_anchor, start_index + len(start_anchor))
        if end_index < 0:
            return Observation(
                False,
                f"replace_block miss {_rel_path(abs_path, project_root)}",
                changed=False,
                details="end_anchor not found after start_anchor.",
                tool="replace_block",
                path=abs_path,
            )
    elif end_line is not None:
        end_anchor, end_index, _, error = _resolve_legacy_line_reference(current, end_line, "end_line")
        if error:
            return Observation(
                False,
                f"replace_block failed {_rel_path(abs_path, project_root)}",
                changed=False,
                details=error,
                tool="replace_block",
                path=abs_path,
            )
        if end_index < start_index:
            return Observation(
                False,
                f"replace_block failed {_rel_path(abs_path, project_root)}",
                changed=False,
                details="end_line must be after start_line.",
                tool="replace_block",
                path=abs_path,
            )
    else:
        return Observation(
            False,
            f"replace_block failed {_rel_path(abs_path, project_root)}",
            changed=False,
            details="Missing start_anchor or end_anchor.",
            tool="replace_block",
            path=abs_path,
        )

    # Replace the block from start_anchor up to just before end_anchor.
    updated = current[:start_index] + replacement + current[end_index:]
    if updated == current:
        return Observation(True, f"replace_block no-op {_rel_path(abs_path, project_root)}", changed=False, tool="replace_block", path=abs_path)

    _write_text(abs_path, updated)
    return Observation(True, f"replace_block updated {_rel_path(abs_path, project_root)}", changed=True, tool="replace_block", path=abs_path)


def begin_file_rewrite(path, expected_parts, project_root=None, patch_mode=False, allow_create=True):
    try:
        abs_path = _resolve_path(path, project_root=project_root)
    except Exception as e:
        return Observation(False, f"begin_file_rewrite failed {path}", changed=False, details=str(e), tool="begin_file_rewrite", path=str(path))

    existed = os.path.exists(abs_path)
    if patch_mode and not existed and not allow_create:
        return Observation(
            False,
            f"begin_file_rewrite blocked {_rel_path(abs_path, project_root)}",
            changed=False,
            details="Patch mode cannot create new files unless explicitly requested.",
            tool="begin_file_rewrite",
            path=abs_path,
        )

    try:
        expected_parts = int(expected_parts)
    except Exception:
        return Observation(
            False,
            f"begin_file_rewrite failed {_rel_path(abs_path, project_root)}",
            changed=False,
            details="expected_parts must be an integer.",
            tool="begin_file_rewrite",
            path=abs_path,
        )
    if expected_parts < 1 or expected_parts > 200:
        return Observation(
            False,
            f"begin_file_rewrite failed {_rel_path(abs_path, project_root)}",
            changed=False,
            details="expected_parts must be between 1 and 200.",
            tool="begin_file_rewrite",
            path=abs_path,
        )

    state = {
        "path": abs_path,
        "expected_parts": expected_parts,
        "parts": {},
    }
    _save_rewrite_state(abs_path, project_root, state)
    return Observation(
        True,
        f"begin_file_rewrite {_rel_path(abs_path, project_root)} parts={expected_parts}",
        changed=True,
        tool="begin_file_rewrite",
        path=abs_path,
        metadata={"progress": True, "touches_file": False},
    )


def append_file_chunk(path, part, content, project_root=None, content_b64=None):
    try:
        abs_path = _resolve_path(path, project_root=project_root)
    except Exception as e:
        return Observation(False, f"append_file_chunk failed {path}", changed=False, details=str(e), tool="append_file_chunk", path=str(path))

    state = _load_rewrite_state(abs_path, project_root)
    if not state:
        return Observation(
            False,
            f"append_file_chunk failed {_rel_path(abs_path, project_root)}",
            changed=False,
            details="Missing rewrite state. Call begin_file_rewrite first.",
            tool="append_file_chunk",
            path=abs_path,
        )

    try:
        expected_parts = int(state.get("expected_parts"))
    except Exception:
        return Observation(
            False,
            f"append_file_chunk failed {_rel_path(abs_path, project_root)}",
            changed=False,
            details="Corrupted rewrite state: expected_parts is invalid.",
            tool="append_file_chunk",
            path=abs_path,
        )
    try:
        part = int(part)
    except Exception:
        return Observation(
            False,
            f"append_file_chunk failed {_rel_path(abs_path, project_root)}",
            changed=False,
            details="part must be an integer.",
            tool="append_file_chunk",
            path=abs_path,
        )
    if part < 1 or part > expected_parts:
        return Observation(
            False,
            f"append_file_chunk failed {_rel_path(abs_path, project_root)}",
            changed=False,
            details=f"part must be in range 1..{expected_parts}.",
            tool="append_file_chunk",
            path=abs_path,
        )

    parts = state.get("parts")
    if not isinstance(parts, dict):
        parts = {}
    decoded_content, decode_error = _decode_b64_text(content_b64, "content_b64")
    if decode_error:
        return Observation(
            False,
            f"append_file_chunk failed {_rel_path(abs_path, project_root)}",
            changed=False,
            details=decode_error,
            tool="append_file_chunk",
            path=abs_path,
        )
    if decoded_content is not None:
        content = decoded_content

    chunk = str(content or "")
    key = str(part)
    previous = parts.get(key)
    parts[key] = chunk
    state["parts"] = parts
    _save_rewrite_state(abs_path, project_root, state)

    received = len(parts)
    changed = previous != chunk
    return Observation(
        True,
        f"append_file_chunk {_rel_path(abs_path, project_root)} part={part}/{expected_parts} received={received}",
        changed=changed,
        tool="append_file_chunk",
        path=abs_path,
        metadata={"progress": True, "touches_file": False},
    )


def finalize_file_rewrite(path, project_root=None, patch_mode=False, allow_create=True):
    try:
        abs_path = _resolve_path(path, project_root=project_root)
    except Exception as e:
        return Observation(False, f"finalize_file_rewrite failed {path}", changed=False, details=str(e), tool="finalize_file_rewrite", path=str(path))

    state = _load_rewrite_state(abs_path, project_root)
    if not state:
        return Observation(
            False,
            f"finalize_file_rewrite failed {_rel_path(abs_path, project_root)}",
            changed=False,
            details="Missing rewrite state. Call begin_file_rewrite first.",
            tool="finalize_file_rewrite",
            path=abs_path,
        )

    try:
        expected_parts = int(state.get("expected_parts"))
    except Exception:
        return Observation(
            False,
            f"finalize_file_rewrite failed {_rel_path(abs_path, project_root)}",
            changed=False,
            details="Corrupted rewrite state: expected_parts is invalid.",
            tool="finalize_file_rewrite",
            path=abs_path,
        )
    parts = state.get("parts")
    if not isinstance(parts, dict):
        parts = {}

    missing = [str(index) for index in range(1, expected_parts + 1) if str(index) not in parts]
    if missing:
        return Observation(
            False,
            f"finalize_file_rewrite incomplete {_rel_path(abs_path, project_root)}",
            changed=False,
            details="Missing parts: " + ", ".join(missing),
            tool="finalize_file_rewrite",
            path=abs_path,
        )

    content = "".join(str(parts[str(index)]) for index in range(1, expected_parts + 1))
    if not content.strip():
        return Observation(
            False,
            f"finalize_file_rewrite failed {_rel_path(abs_path, project_root)}",
            changed=False,
            details="Assembled content is empty.",
            tool="finalize_file_rewrite",
            path=abs_path,
        )

    existed = os.path.exists(abs_path)
    if patch_mode and not existed and not allow_create:
        return Observation(
            False,
            f"finalize_file_rewrite blocked {_rel_path(abs_path, project_root)}",
            changed=False,
            details="Patch mode cannot create new files unless explicitly requested.",
            tool="finalize_file_rewrite",
            path=abs_path,
        )

    old_content = _read_file(abs_path) if existed else ""
    content, normalization_note = _normalize_shell_script_content(abs_path, content)
    if patch_mode and existed and not _looks_like_full_file_rewrite(old_content, content):
        return Observation(
            False,
            f"finalize_file_rewrite rejected {_rel_path(abs_path, project_root)}",
            changed=False,
            details="Assembled file does not look like a full corrected file.",
            tool="finalize_file_rewrite",
            path=abs_path,
        )

    if existed and old_content == content:
        _clear_rewrite_state(abs_path, project_root)
        return Observation(
            True,
            f"finalize_file_rewrite no-op {_rel_path(abs_path, project_root)}",
            changed=False,
            tool="finalize_file_rewrite",
            path=abs_path,
            metadata={"touches_file": True},
        )

    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".rewrite_", suffix=".tmp", dir=parent or None, text=True)
    os.close(tmp_fd)
    try:
        _write_text(tmp_path, content)
        os.replace(tmp_path, abs_path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

    _clear_rewrite_state(abs_path, project_root)
    written_content = _read_file(abs_path)
    details = normalization_note
    warning = _shell_write_warning(abs_path, written_content)
    if warning:
        details = (details + "\n" if details else "") + warning
    return Observation(
        True,
        f"finalize_file_rewrite wrote {_rel_path(abs_path, project_root)}",
        changed=True,
        details=details,
        tool="finalize_file_rewrite",
        path=abs_path,
        metadata={"touches_file": True},
    )


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


def _looks_like_url_token(arg):
    text = str(arg or "").strip().strip('"').strip("'")
    lowered = text.lower()
    if not lowered:
        return False
    if lowered.startswith(("http://", "https://", "ftp://", "www.")):
        return True
    if "://" in lowered:
        return True
    if lowered.startswith(("http:\\\\", "https:\\\\", "ftp:\\\\")):
        return True
    return False


def _normalize_malformed_url_token(arg):
    text = str(arg or "").strip()
    lowered = text.lower()
    if lowered.startswith(("http:\\\\", "https:\\\\", "ftp:\\\\")):
        fixed = text.replace("\\", "/")
        if fixed.lower().startswith("http:/") and not fixed.lower().startswith("http://"):
            fixed = "http://" + fixed[6:]
        elif fixed.lower().startswith("https:/") and not fixed.lower().startswith("https://"):
            fixed = "https://" + fixed[7:]
        elif fixed.lower().startswith("ftp:/") and not fixed.lower().startswith("ftp://"):
            fixed = "ftp://" + fixed[5:]
        return fixed
    return text


def _normalize_windows_cmd_invocation(args):
    if not isinstance(args, list) or not args:
        return args
    normalized = [str(part) for part in args]
    first = os.path.basename(normalized[0]).lower().replace(".exe", "")
    if first == "cmd" and len(normalized) > 1:
        second = normalized[1].strip().lower()
        if second in {"\\c", "\\\\c"}:
            normalized[1] = "/c"
    for i in range(1, len(normalized)):
        if _looks_like_url_token(normalized[i]):
            normalized[i] = _normalize_malformed_url_token(normalized[i])
    return normalized


def _validate_cmd_paths(args, project_root):
    if not project_root:
        return True, ""

    root = os.path.abspath(project_root)
    first_cmd = os.path.basename(str(args[0] if args else "")).lower().replace(".exe", "")
    for arg in args[1:]:
        text = str(arg)
        if text.startswith("-"):
            continue
        if first_cmd == "cmd" and text.strip().lower() in {"/c", "/k", "\\c", "\\k"}:
            continue
        if _looks_like_url_token(text):
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

    cmd = _normalize_windows_cmd_invocation([str(part) for part in cmd])
    ok, error = _validate_cmd_paths(cmd, project_root=project_root)
    if not ok:
        return None, error

    return cmd, ""


def _normalize_simple_windows_launch(cmd, cwd=None, project_root=None):
    if not isinstance(cmd, str):
        return cmd, cwd, ""
    raw = str(cmd).strip()
    if "&&" not in raw:
        return cmd, cwd, ""
    if any(token in raw for token in ("||", ";", "|", ">", "<")):
        return cmd, cwd, ""
    match = WINDOWS_SIMPLE_LAUNCH_RE.match(raw)
    if not match:
        return cmd, cwd, ""

    target_text = (match.group(1) or "").strip().strip('"').strip("'")
    remainder = (match.group(2) or "").strip()
    if not remainder:
        return cmd, cwd, ""

    normalized_cwd = cwd
    base_root = project_root or cwd or os.getcwd()
    if target_text.lower() == "%~dp0":
        normalized_cwd = base_root
    else:
        try:
            normalized_cwd = _resolve_path(target_text, project_root=base_root)
        except Exception:
            return cmd, cwd, ""
    return remainder, normalized_cwd, "Normalized simple Windows launch command."


def run_cmd(cmd, cwd=None, project_root=None, stop_checker=None):
    project_root = project_root or cwd
    normalized_cwd = cwd
    if normalized_cwd:
        try:
            normalized_cwd = _resolve_path(normalized_cwd, project_root=project_root or normalized_cwd)
        except Exception as exc:
            return Observation(False, f"CMD BLOCKED: {cmd}", changed=False, details=str(exc), tool="run_cmd")
    normalization_note = ""
    prepared, error = _prepare_cmd(cmd, project_root=project_root)
    if error:
        return Observation(False, f"CMD BLOCKED: {cmd}", changed=False, details=error, tool="run_cmd")

    try:
        process = subprocess.Popen(
            prepared,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=normalized_cwd,
        )
        timeout_seconds = max(1, int(config.RUN_TIMEOUT or 15))
        deadline = time.monotonic() + timeout_seconds
        while True:
            if callable(stop_checker) and stop_checker():
                process.kill()
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    stdout, stderr = process.communicate()
                details = ((stdout or "") + "\n" + (stderr or "")).strip()[:4000]
                return Observation(False, "CMD KILLED BY USER", changed=False, details=details, tool="run_cmd")
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                details = ((stdout or "") + "\n" + (stderr or "")).strip()[:4000]
                break
            if time.monotonic() >= deadline:
                process.kill()
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    stdout, stderr = process.communicate()
                details = ((stdout or "") + "\n" + (stderr or "")).strip()[:4000]
                return Observation(False, f"CMD TIMEOUT: {prepared}", changed=False, details=details, tool="run_cmd")
            time.sleep(0.2)
        if normalization_note:
            prefix = normalization_note
            if normalized_cwd:
                prefix += f" cwd={normalized_cwd}"
            details = (prefix + "\n" + details).strip()
        return Observation(
            process.returncode == 0,
            f"CMD exit={process.returncode}: {prepared}",
            changed=False,
            details=details,
            tool="run_cmd",
            metadata={"returncode": process.returncode},
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

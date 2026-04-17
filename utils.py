import json
import os
import subprocess
import sys


def ensure_gitignore(repo_dir):
    path = os.path.join(repo_dir, ".gitignore")
    entries = [
        "__pycache__/",
        "*.pyc",
        ".agent/",
        "_tmp_validation/",
        "pipeline_log.txt",
        ".env",
        ".venv/",
        "venv/",
    ]

    existing = ""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            existing = f.read()

    with open(path, "a", encoding="utf-8") as f:
        for entry in entries:
            if entry not in existing:
                if existing and not existing.endswith("\n"):
                    f.write("\n")
                f.write(entry + "\n")
                existing += entry + "\n"


def open_path(path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def approx_token_count(text):
    text = text or ""
    return max(1, (len(text) + 3) // 4) if text else 0


def truncate_middle(text, max_chars):
    text = text or ""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= 8:
        return text[:max_chars]
    keep = max_chars - 5
    head = keep // 2
    tail = keep - head
    return text[:head] + "\n...\n" + text[-tail:]


def safe_read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def is_subpath(parent, child):
    parent = os.path.normcase(os.path.realpath(parent))
    child = os.path.normcase(os.path.realpath(child))
    return child == parent or child.startswith(parent + os.sep)


def coerce_int(value, default, minimum=None, maximum=None):
    try:
        number = int(str(value).strip())
    except Exception:
        number = default

    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def read_json_file(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json_file(path, data):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)

import os
import sys
import subprocess


def ensure_gitignore(repo_dir):
    path = os.path.join(repo_dir, ".gitignore")
    entries = [
        "__pycache__/",
        "*.pyc",
        "TEST/",
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

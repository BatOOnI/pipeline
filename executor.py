import os
import subprocess
import config
from contracts import Observation


def write_file(path, content):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    old = None
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            old = f.read()

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    return Observation(True, f"Wrote {path}", changed=(old != content))


def normalize_cmd_platform(cmd):
    if isinstance(cmd, list):
        fixed = []
        for x in cmd:
            if isinstance(x, str):
                if x == "python3":
                    x = "python"
            fixed.append(x)
        return fixed

    if isinstance(cmd, str):
        return cmd.replace("python3", "python")

    return cmd


def run_cmd(cmd, cwd=None):
    cmd = normalize_cmd_platform(cmd)

    try:
        if isinstance(cmd, list):
            result = subprocess.run(
                cmd,
                shell=False,
                capture_output=True,
                text=True,
                cwd=cwd,
                input="",
                timeout=config.RUN_TIMEOUT
            )
        else:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                cwd=cwd,
                input="",
                timeout=config.RUN_TIMEOUT
            )

        return Observation(
            result.returncode == 0,
            f"CMD exit={result.returncode}: {cmd}",
            changed=True,
            details=((result.stdout or "") + "\n" + (result.stderr or ""))[:4000]
        )
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        err = e.stderr or ""
        return Observation(
            False,
            f"CMD TIMEOUT: {cmd}",
            changed=False,
            details=(str(out) + "\n" + str(err))[:4000]
        )
    except Exception as e:
        return Observation(False, f"CMD FAILED: {cmd}", changed=False, details=str(e))

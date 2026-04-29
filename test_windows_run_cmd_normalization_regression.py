import os
import shutil
import tempfile

import config
from agent_loop import PipelineState, _execute_action
from executor import _prepare_cmd


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def _state(root):
    return PipelineState(
        goal="windows-run-cmd-normalization",
        mode="create",
        active_project_root=root,
        current_provider="lmstudio",
    )


def main():
    root = tempfile.mkdtemp(prefix="win_cmd_norm_")
    backup = {}
    try:
        _set_config("PERMISSION_MODE", "allow", backup)
        _set_config("PERMISSION_ALLOW_RULES", "", backup)
        _set_config("PERMISSION_DENY_RULES", "", backup)
        _set_config("PERMISSION_ASK_RULES", "", backup)
        _set_config("NETWORK_ENABLED", True, backup)

        prepared, error = _prepare_cmd("cmd \\c dir", project_root=root)
        assert not error, f"cmd \\c should normalize, got error: {error}"
        assert prepared[0].lower().endswith("cmd"), "expected cmd executable"
        assert prepared[1] == "/c", "expected cmd \\c normalized to cmd /c"

        url_cmd = "git clone https://github.com/rm-hull/luma.emulator.git luma.emulator"
        prepared_url, url_error = _prepare_cmd(url_cmd, project_root=root)
        assert not url_error, f"valid URL should not be blocked: {url_error}"
        assert (
            "https://github.com/rm-hull/luma.emulator.git" in prepared_url
        ), "URL slashes should be preserved"

        bad_url_cmd = r"git clone https:\\github.com\owner\repo.git repo"
        prepared_bad_url, bad_url_error = _prepare_cmd(bad_url_cmd, project_root=root)
        assert not bad_url_error, f"malformed backslash URL should normalize or produce clear non-crash error: {bad_url_error}"
        assert (
            "https://github.com/owner/repo.git" in prepared_bad_url
        ), "backslash URL should normalize to forward slashes"

        prepared_escape, escape_error = _prepare_cmd(r"python \B", project_root=root)
        assert prepared_escape is None and "Path escapes project root" in str(
            escape_error or ""
        ), r"\B must remain blocked as path escape"

        prepared_abs, abs_error = _prepare_cmd(r"python C:\outside\test.py", project_root=root)
        assert prepared_abs is None and "Path escapes project root" in str(
            abs_error or ""
        ), r"C:\outside must remain blocked as path escape"

        state = _state(root)
        allowed_cwd = _execute_action(
            {"type": "run_cmd", "args": {"cmd": ["python", "-c", "print('ok')"], "cwd": root}},
            state,
            project_root_override=root,
        )
        assert allowed_cwd.tool == "run_cmd", "cwd inside project should remain allowed"
    finally:
        for key, value in backup.items():
            setattr(config, key, value)
        shutil.rmtree(root, ignore_errors=True)

    print("OK: windows run_cmd normalization regression checks passed")


if __name__ == "__main__":
    main()

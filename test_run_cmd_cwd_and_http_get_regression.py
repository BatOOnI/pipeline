import os
import shutil
import tempfile

import config
from agent_loop import PipelineState, _execute_action


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def _state(root):
    return PipelineState(
        goal="run-cmd-cwd-http-get-regression",
        mode="create",
        active_project_root=root,
        current_provider="lmstudio",
    )


def main():
    root = tempfile.mkdtemp(prefix="run_cmd_cwd_http_get_")
    backup = {}
    try:
        _set_config("PERMISSION_MODE", "allow", backup)
        _set_config("PERMISSION_ALLOW_RULES", "", backup)
        _set_config("PERMISSION_DENY_RULES", "", backup)
        _set_config("PERMISSION_ASK_RULES", "", backup)
        _set_config("NETWORK_ENABLED", True, backup)
        _set_config("RUN_TIMEOUT", 15, backup)
        state = _state(root)

        ok_run = _execute_action(
            {"type": "run_cmd", "args": {"cmd": ["python", "-c", "print('ok')"], "cwd": root}},
            state,
            project_root_override=root,
        )
        assert ok_run.tool == "run_cmd", "run_cmd with cwd should execute via run_cmd tool"
        assert "CMD exit=" in str(ok_run.summary or ""), "run_cmd summary should include process exit"

        blocked_chain = _execute_action(
            {"type": "run_cmd", "args": {"cmd": f'cd /d "{root}" && dir', "cwd": root}},
            state,
            project_root_override=root,
        )
        assert blocked_chain.tool == "run_cmd", "shell-operator chain should be blocked by run_cmd validator"
        assert "Shell operators are not allowed in run_cmd" in str(
            blocked_chain.details or ""
        ), "expected explicit guidance to avoid shell operators"

        outside = os.path.abspath(os.path.join(root, ".."))
        blocked_cwd = _execute_action(
            {"type": "run_cmd", "args": {"cmd": "cmd /c dir", "cwd": outside}},
            state,
            project_root_override=root,
        )
        assert blocked_cwd.tool == "run_cmd", "cwd escape should be blocked in run_cmd"
        assert "Path escapes project root" in str(
            blocked_cwd.details or ""
        ), "expected explicit cwd escape block reason"

        missing_output = _execute_action(
            {"type": "http_get", "args": {"url": "https://example.com"}},
            state,
            project_root_override=root,
        )
        assert missing_output.tool == "http_get", "http_get validation should return normal tool observation"
        assert "http_get requires url and output_path" in str(
            missing_output.details or ""
        ), "missing output_path should not crash and should show clear message"

        output_rel = os.path.join("downloads", "example.html")
        attempted_get = _execute_action(
            {
                "type": "http_get",
                "args": {"url": "https://example.com", "output_path": output_rel},
            },
            state,
            project_root_override=root,
        )
        assert attempted_get.tool in {
            "http_get",
            "permission_denied",
        }, "http_get with url+output_path should be attempted or policy-denied, never crash"
    finally:
        for key, value in backup.items():
            setattr(config, key, value)
        shutil.rmtree(root, ignore_errors=True)

    print("OK: run_cmd cwd and http_get regression checks passed")


if __name__ == "__main__":
    main()

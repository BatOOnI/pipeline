import os
import shutil
import tempfile

import config
from agent_loop import PipelineState, _execute_action


def _state(root):
    return PipelineState(
        goal="perm-test",
        mode="create",
        active_project_root=root,
        current_provider="lmstudio",
    )


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def main():
    root = tempfile.mkdtemp(prefix="perm_flow_")
    backup = {}
    try:
        _set_config("PERMISSION_MODE", "read-only", backup)
        _set_config("PERMISSION_ALLOW_RULES", "", backup)
        _set_config("PERMISSION_DENY_RULES", "", backup)
        _set_config("PERMISSION_ASK_RULES", "", backup)
        _set_config("NETWORK_ENABLED", True, backup)

        state = _state(root)
        blocked = _execute_action(
            {"type": "write_file", "args": {"path": "a.txt", "content": "hi"}},
            state,
            project_root_override=root,
        )
        assert blocked.tool == "permission_denied", "write_file should be blocked in read-only mode"

        allowed_read = _execute_action(
            {"type": "read_file", "args": {"path": "a.txt"}},
            state,
            project_root_override=root,
        )
        assert allowed_read.tool == "read_file", "read_file should be allowed in read-only mode"

        _set_config("PERMISSION_MODE", "workspace-write", backup)
        state = _state(root)
        write_ok = _execute_action(
            {"type": "write_file", "args": {"path": "a.txt", "content": "hi"}},
            state,
            project_root_override=root,
        )
        assert write_ok.ok, "write_file should be allowed in workspace-write mode"

        _set_config("PERMISSION_DENY_RULES", "write_file:*", backup)
        state = _state(root)
        denied_by_rule = _execute_action(
            {"type": "write_file", "args": {"path": "b.txt", "content": "x"}},
            state,
            project_root_override=root,
        )
        assert denied_by_rule.tool == "permission_denied", "deny rule should block write_file"

        _set_config("PERMISSION_DENY_RULES", "", backup)
        _set_config("PERMISSION_ASK_RULES", "run_cmd:*", backup)
        ask_blocked = _execute_action(
            {"type": "run_cmd", "args": {"cmd": ["python", "-c", "print('ok')"]}},
            state,
            project_root_override=root,
        )
        assert ask_blocked.tool == "permission_denied", "ask rule should block without decider"

        ask_allowed = _execute_action(
            {"type": "run_cmd", "args": {"cmd": ["python", "-c", "print('ok')"]}},
            state,
            project_root_override=root,
            permission_decider=lambda req: "allow",
        )
        assert ask_allowed.tool == "run_cmd", "ask rule should pass with allow decider"

        _set_config("PERMISSION_ASK_RULES", "", backup)
        _set_config("PERMISSION_MODE", "allow", backup)
        _set_config("NETWORK_ENABLED", False, backup)
        network_blocked = _execute_action(
            {"type": "run_cmd", "args": {"cmd": "curl https://example.com"}},
            state,
            project_root_override=root,
        )
        assert network_blocked.tool == "permission_denied", "network run_cmd should be blocked when NETWORK_ENABLED is off"
        assert "Network access is disabled" in str(network_blocked.details or ""), "network block reason should be explicit"

        _set_config("NETWORK_ENABLED", True, backup)
        destructive_outside = _execute_action(
            {"type": "run_cmd", "args": {"cmd": "Remove-Item C:\\Windows\\temp.txt"}},
            state,
            project_root_override=root,
        )
        assert destructive_outside.tool == "permission_denied", "destructive path outside workspace should be blocked"
        assert "outside workspace" in str(destructive_outside.details or "").lower(), "outside-workspace reason should be explicit"

    finally:
        for key, value in backup.items():
            setattr(config, key, value)
        shutil.rmtree(root, ignore_errors=True)

    print("OK: permission flow regression checks passed")


if __name__ == "__main__":
    main()

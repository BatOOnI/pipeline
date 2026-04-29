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
        goal="net-tools-test",
        mode="create",
        active_project_root=root,
        current_provider="lmstudio",
    )


def main():
    root = tempfile.mkdtemp(prefix="net_tools_perm_")
    backup = {}
    try:
        _set_config("PERMISSION_ALLOW_RULES", "", backup)
        _set_config("PERMISSION_DENY_RULES", "", backup)
        _set_config("PERMISSION_ASK_RULES", "", backup)

        _set_config("PERMISSION_MODE", "allow", backup)
        _set_config("NETWORK_ENABLED", False, backup)
        state = _state(root)
        blocked_http = _execute_action(
            {"type": "http_get", "args": {"url": "https://example.com"}},
            state,
            project_root_override=root,
        )
        assert blocked_http.tool == "permission_denied", "http_get should be denied when network toggle is off"
        assert "Network access is disabled" in str(blocked_http.details or ""), "expected explicit network-disabled reason"

        _set_config("NETWORK_ENABLED", True, backup)
        _set_config("PERMISSION_MODE", "read-only", backup)
        blocked_download_mode = _execute_action(
            {"type": "download_file", "args": {"url": "https://example.com/a.txt", "path": "a.txt"}},
            state,
            project_root_override=root,
        )
        assert blocked_download_mode.tool == "permission_denied", "download_file should require workspace-write mode"
        assert "requires 'workspace-write'" in str(blocked_download_mode.details or ""), "expected workspace-write permission reason"

        _set_config("PERMISSION_MODE", "workspace-write", backup)
        _set_config("NETWORK_ENABLED", False, backup)
        blocked_download_network = _execute_action(
            {"type": "download_file", "args": {"url": "https://example.com/a.txt", "path": "a.txt"}},
            state,
            project_root_override=root,
        )
        assert blocked_download_network.tool == "permission_denied", "download_file should be denied when network toggle is off"
        assert "Network access is disabled" in str(blocked_download_network.details or ""), "expected explicit network-disabled reason"
    finally:
        for key, value in backup.items():
            setattr(config, key, value)
        shutil.rmtree(root, ignore_errors=True)

    print("OK: network tool permission regression checks passed")


if __name__ == "__main__":
    main()

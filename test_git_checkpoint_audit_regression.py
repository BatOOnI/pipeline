import os
import shutil
import uuid

import agent_loop
import config
from git_tools import git_checkpoint, git_init


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def main():
    root = os.path.join(os.getcwd(), "TEST", f"git_checkpoint_audit_{uuid.uuid4().hex[:8]}")
    os.makedirs(root, exist_ok=True)

    logs = []
    config_backup = {}
    original_call_model = agent_loop.call_model
    call_count = {"value": 0}

    responses = [
        '{"actions":[{"type":"write_file","args":{"path":"audit.txt","content":"hello\\n"}}]}',
        "done",
    ]

    def fake_call_model(*args, **kwargs):
        idx = call_count["value"]
        call_count["value"] += 1
        if idx < len(responses):
            return responses[idx]
        return "done"

    try:
        _set_config("PROJECT_ROOT", root, config_backup)
        _set_config("SESSION_FILE", ".agent/session_git_checkpoint_audit.json", config_backup)
        _set_config("PATCH_FILES", "", config_backup)
        _set_config("MODE_CONTROL", "AUTO", config_backup)
        _set_config("RESCUE_MODE", "OFF", config_backup)
        _set_config("MAX_ITERATIONS", 8, config_backup)
        _set_config("AUTO_VERIFY_PYTHON", False, config_backup)
        _set_config("AUTO_GIT_CHECKPOINTS", True, config_backup)
        _set_config("ALLOW_EMPTY_DONE_RETRY", False, config_backup)

        agent_loop.call_model = fake_call_model
        agent_loop.run("create audit.txt", logger=lambda msg: logs.append(str(msg)))

        target_lines = [line for line in logs if line.startswith("GIT CHECKPOINT TARGET:")]
        assert target_lines, "checkpoint target log should be present"
        assert any(f"selected_root={os.path.abspath(root)}" in line for line in target_lines), "selected root should be logged"
        assert any(f"repo_root={os.path.abspath(root)}" in line for line in logs), "repo_root should match selected root"
        assert any("branch=" in line for line in logs), "branch should be logged"
        assert any("remote=" in line for line in logs), "remote should be logged"

        # Guard check: if target path is nested inside an existing repo root, checkpoint must initialize/use local repo.
        parent = os.path.join(root, "parent_repo")
        child = os.path.join(parent, "nested_target")
        os.makedirs(child, exist_ok=True)
        ok, msg = git_init(parent)
        assert ok, f"git init should succeed for audit guard setup: {msg}"
        ok2, msg2 = git_checkpoint(child, "checkpoint mismatch guard")
        assert ok2, f"checkpoint should initialize/use local nested repo root: {msg2}"
        assert f"repo_root={os.path.abspath(child)}" in str(msg2), f"nested repo root should be explicit: {msg2}"
    finally:
        agent_loop.call_model = original_call_model
        for key, value in config_backup.items():
            setattr(config, key, value)
        shutil.rmtree(root, ignore_errors=True)

    print("OK: git checkpoint audit regression checks passed")


if __name__ == "__main__":
    main()

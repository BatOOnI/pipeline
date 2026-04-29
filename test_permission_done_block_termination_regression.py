import os
import shutil
import uuid

import agent_loop
import config


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def main():
    root = os.path.join(os.getcwd(), "TEST", f"perm_done_stop_{uuid.uuid4().hex[:8]}")
    os.makedirs(root, exist_ok=True)

    logs = []
    config_backup = {}
    original_call_model = agent_loop.call_model
    call_count = {"value": 0}

    responses = [
        '{"actions":[{"type":"run_cmd","args":{"cmd":"git reset --hard"}}]}',
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
        _set_config("SESSION_FILE", ".agent/session_perm_done_stop.json", config_backup)
        _set_config("PATCH_FILES", "", config_backup)
        _set_config("MODE_CONTROL", "AUTO", config_backup)
        _set_config("RESCUE_MODE", "OFF", config_backup)
        _set_config("MAX_ITERATIONS", 12, config_backup)
        _set_config("AUTO_VERIFY_PYTHON", False, config_backup)
        _set_config("AUTO_GIT_CHECKPOINTS", False, config_backup)
        _set_config("ALLOW_EMPTY_DONE_RETRY", False, config_backup)
        _set_config("PERMISSION_MODE", "workspace-write", config_backup)
        _set_config("PERMISSION_ALLOW_RULES", "", config_backup)
        _set_config("PERMISSION_DENY_RULES", "", config_backup)
        _set_config("PERMISSION_ASK_RULES", "", config_backup)

        agent_loop.call_model = fake_call_model
        agent_loop.run("run git reset --hard", logger=lambda msg: logs.append(str(msg)))

        assert any("PERMISSION DENIED" in line for line in logs), "permission deny should be reported"
        assert any("EMPTY DONE AFTER PERMISSION BLOCK -> STOP" in line for line in logs), "blocked done path should terminate cleanly"
        assert sum(1 for line in logs if "EMPTY DONE BLOCKED: runtime or verify error still exists" in line) <= 1, (
            "legacy empty-done block loop should not repeat"
        )
        assert any(
            "RUN SUMMARY:" in line
            and "status=stopped" in line
            and "stop_reason=permission policy blocked execution" in line
            for line in logs
        ), "run summary should clearly report permission-policy stop"
        assert call_count["value"] == 2, f"unexpected retry loop count: {call_count['value']}"
    finally:
        agent_loop.call_model = original_call_model
        for key, value in config_backup.items():
            setattr(config, key, value)
        shutil.rmtree(root, ignore_errors=True)

    print("OK: permission done-block termination regression checks passed")


if __name__ == "__main__":
    main()

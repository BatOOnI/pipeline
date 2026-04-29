import os
import shutil
import uuid

import agent_loop
import config


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def main():
    root = os.path.join(os.getcwd(), "TEST", f"create_python_verify_stop_{uuid.uuid4().hex[:8]}")
    os.makedirs(root, exist_ok=True)

    logs = []
    backup = {}
    original_call_model = agent_loop.call_model
    call_count = {"value": 0}

    responses = [
        '{"actions":[{"type":"write_file","args":{"path":"calc_app.py","content":"x = 4\\ny = 5\\nprint(x + y)\\n"}}]}',
        "done",
    ]

    def fake_call_model(*args, **kwargs):
        idx = call_count["value"]
        call_count["value"] += 1
        if idx < len(responses):
            return responses[idx]
        return "done"

    try:
        _set_config("PROJECT_ROOT", root, backup)
        _set_config("SESSION_FILE", ".agent/session_create_python_verify_stop.json", backup)
        _set_config("PATCH_FILES", "", backup)
        _set_config("MODE_CONTROL", "AUTO", backup)
        _set_config("RESCUE_MODE", "OFF", backup)
        _set_config("MAX_ITERATIONS", 8, backup)
        _set_config("AUTO_VERIFY_PYTHON", True, backup)
        _set_config("AUTO_GIT_CHECKPOINTS", False, backup)
        _set_config("ALLOW_EMPTY_DONE_RETRY", False, backup)

        agent_loop.call_model = fake_call_model
        agent_loop.run("create calc_app.py that prints a simple sum", logger=lambda msg: logs.append(str(msg)))

        output_path = os.path.join(root, "calc_app.py")
        assert os.path.exists(output_path), "calc_app.py missing"
        assert any("VERIFY OK: calc_app.py" in line for line in logs), "python verify should pass"
        assert any("CREATE OUTPUTS VERIFIED -> STOP" in line for line in logs), "should stop after verified create output"
        assert any("RUN SUMMARY:" in line and "status=success" in line for line in logs), "run should finish successfully"
        assert call_count["value"] == 1, f"unexpected extra iterations: {call_count['value']}"
    finally:
        agent_loop.call_model = original_call_model
        for key, value in backup.items():
            setattr(config, key, value)
        shutil.rmtree(root, ignore_errors=True)

    print("OK: create python verify stop regression checks passed")


if __name__ == "__main__":
    main()

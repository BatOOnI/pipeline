import os
import shutil
import uuid

import agent_loop
import config


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def main():
    root = os.path.join(os.getcwd(), "TEST", f"existing_improve_patch_route_{uuid.uuid4().hex[:8]}")
    os.makedirs(root, exist_ok=True)
    app_path = os.path.join(root, "calc_app.py")
    with open(app_path, "w", encoding="utf-8") as handle:
        handle.write("def calc(a, b):\n    return a + b\n\nprint(calc(2, 3))\n")

    logs = []
    backup = {}
    original_call_model = agent_loop.call_model
    call_count = {"value": 0}

    improved = (
        "def calc(a, b):\n"
        "    # small safe improvement: normalize to int\n"
        "    return int(a) + int(b)\n\n"
        "print(calc(2, 3))\n"
    )
    responses = [
        (
            '{"actions":['
            '{"type":"read_file","args":{"path":"calc_app.py"}},'
            '{"type":"write_file","args":{"path":"calc_app.py","content":"'
            + improved.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            + '"}}]}'
        ),
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
        _set_config("SESSION_FILE", ".agent/session_existing_improve_patch_route.json", backup)
        _set_config("PATCH_FILES", "", backup)
        _set_config("MODE_CONTROL", "AUTO", backup)
        _set_config("RESCUE_MODE", "OFF", backup)
        _set_config("MAX_ITERATIONS", 8, backup)
        _set_config("AUTO_VERIFY_PYTHON", True, backup)
        _set_config("AUTO_GIT_CHECKPOINTS", False, backup)
        _set_config("ALLOW_EMPTY_DONE_RETRY", False, backup)

        agent_loop.call_model = fake_call_model
        prompt = (
            "Read existing calc_app.py, make one small safe improvement, "
            "run syntax check, and stop when complete."
        )
        agent_loop.run(prompt, logger=lambda msg: logs.append(str(msg)))

        with open(app_path, "r", encoding="utf-8") as handle:
            final_text = handle.read()

        assert "# small safe improvement" in final_text, "file should be updated"
        assert any("MODE: patch" in line for line in logs), "should route to patch mode"
        assert any("TASK SHAPE: single_file_patch" in line for line in logs), "should classify as single_file_patch"
        assert not any("TASK PROFILE: simple_create" in line for line in logs), "must not route as simple_create"
        assert any("VERIFY OK: calc_app.py" in line for line in logs), "verify should pass"
        assert any("SINGLE-FILE PATCH VERIFIED -> STOP" in line for line in logs), "should stop successfully after verify"
        assert any("RUN SUMMARY:" in line and "status=success" in line for line in logs), "run should finish successfully"
        assert call_count["value"] == 1, f"unexpected extra iterations: {call_count['value']}"
    finally:
        agent_loop.call_model = original_call_model
        for key, value in backup.items():
            setattr(config, key, value)
        shutil.rmtree(root, ignore_errors=True)

    print("OK: existing-file improvement routes to patch regression checks passed")


if __name__ == "__main__":
    main()

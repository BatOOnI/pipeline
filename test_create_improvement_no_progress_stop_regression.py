import os
import shutil
import uuid

import agent_loop
import config


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def main():
    root = os.path.join(os.getcwd(), "TEST", f"create_improve_stop_{uuid.uuid4().hex[:8]}")
    os.makedirs(root, exist_ok=True)
    app_path = os.path.join(root, "app.py")
    with open(app_path, "w", encoding="utf-8") as handle:
        handle.write("def add(a, b):\n    return a + b\n\nprint(add(2, 3))\n")

    logs = []
    backup = {}
    original_call_model = agent_loop.call_model
    call_count = {"value": 0}

    improved = (
        "# tiny safe improvement\n"
        "def add(a, b):\n"
        "    return a + b\n\n"
        "print(add(2, 3))\n"
    )

    responses = [
        (
            '{"actions":['
            '{"type":"read_file","args":{"path":"app.py"}},'
            '{"type":"write_file","args":{"path":"app.py","content":"'
            + improved.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            + '"}}]}'
        ),
        '{"actions":[{"type":"read_file","args":{"path":"app.py"}}]}',
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
        _set_config("SESSION_FILE", ".agent/session_create_improve_stop.json", backup)
        _set_config("PATCH_FILES", "", backup)
        _set_config("MODE_CONTROL", "FORCE_CREATE", backup)
        _set_config("RESCUE_MODE", "OFF", backup)
        _set_config("MAX_ITERATIONS", 8, backup)
        _set_config("AUTO_VERIFY_PYTHON", True, backup)
        _set_config("AUTO_GIT_CHECKPOINTS", False, backup)
        _set_config("ALLOW_EMPTY_DONE_RETRY", False, backup)

        agent_loop.call_model = fake_call_model
        agent_loop.run("improve app.py with a tiny safe readability tweak", logger=lambda msg: logs.append(str(msg)))

        with open(app_path, "r", encoding="utf-8") as handle:
            final_text = handle.read()
        assert "# tiny safe improvement" in final_text, "improvement should be applied"
        assert any("VERIFY OK: app.py" in line for line in logs), "python verify should pass"
        assert any("CREATE OUTPUTS VERIFIED -> STOP" in line for line in logs), "should stop after verified improvement"
        assert any("RUN SUMMARY:" in line and "status=success" in line for line in logs), "run should finish successfully"
        assert call_count["value"] == 1, f"unexpected extra iterations: {call_count['value']}"
    finally:
        agent_loop.call_model = original_call_model
        for key, value in backup.items():
            setattr(config, key, value)
        shutil.rmtree(root, ignore_errors=True)

    print("OK: create improvement no-progress stop regression checks passed")


if __name__ == "__main__":
    main()

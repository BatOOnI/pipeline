import os
import shutil
import uuid

import agent_loop
import config


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def main():
    root = os.path.join(os.getcwd(), "TEST", f"transform_app_summary_{uuid.uuid4().hex[:8]}")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "app.py"), "w", encoding="utf-8") as handle:
        handle.write("def calc(a, b):\n    return a + b\n")

    logs = []
    backup = {}
    original_call_model = agent_loop.call_model
    call_count = {"value": 0}

    responses = [
        '{"actions":[{"type":"write_file","args":{"path":"app_summary.txt","content":"Simple app summary.\\n"}}]}',
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
        _set_config("SESSION_FILE", ".agent/session_transform_app_summary_stop.json", backup)
        _set_config("PATCH_FILES", "", backup)
        _set_config("MODE_CONTROL", "AUTO", backup)
        _set_config("RESCUE_MODE", "OFF", backup)
        _set_config("MAX_ITERATIONS", 8, backup)
        _set_config("AUTO_VERIFY_PYTHON", False, backup)
        _set_config("AUTO_GIT_CHECKPOINTS", False, backup)
        _set_config("ALLOW_EMPTY_DONE_RETRY", False, backup)

        agent_loop.call_model = fake_call_model
        prompt = "Read app.py and create app_summary.txt as derived output summary, then stop."
        agent_loop.run(prompt, logger=lambda msg: logs.append(str(msg)))

        assert os.path.exists(os.path.join(root, "app_summary.txt")), "app_summary.txt missing"
        assert any("TASK SHAPE: transform_copy_task" in line for line in logs), "should route as transform_copy_task"
        assert any("TRANSFORM VERIFY OK" in line for line in logs), "transform verify should pass"
        assert any("TRANSFORM OUTPUTS VERIFIED -> STOP" in line for line in logs), "transform run should stop on verified outputs"
        assert any("RUN SUMMARY:" in line and "status=success" in line for line in logs), "run should finish successfully"
        assert call_count["value"] == 1, f"unexpected extra iterations: {call_count['value']}"
    finally:
        agent_loop.call_model = original_call_model
        for key, value in backup.items():
            setattr(config, key, value)
        shutil.rmtree(root, ignore_errors=True)

    print("OK: transform app_summary stop regression checks passed")


if __name__ == "__main__":
    main()

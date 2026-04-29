import os
import shutil
import uuid

import agent_loop
import config


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def main():
    root = os.path.join(os.getcwd(), "TEST", f"inspect_only_stop_{uuid.uuid4().hex[:8]}")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "calc_app.py"), "w", encoding="utf-8") as handle:
        handle.write("print(1)\n")

    logs = []
    backup = {}
    original_call_model = agent_loop.call_model
    call_count = {"value": 0}

    responses = [
        '{"actions":[{"type":"read_file","args":{"path":"calc_app.py"}}]}',
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
        _set_config("SESSION_FILE", ".agent/session_inspect_only_no_write_stop.json", backup)
        _set_config("PATCH_FILES", "", backup)
        _set_config("MODE_CONTROL", "AUTO", backup)
        _set_config("RESCUE_MODE", "OFF", backup)
        _set_config("MAX_ITERATIONS", 6, backup)
        _set_config("AUTO_VERIFY_PYTHON", False, backup)
        _set_config("AUTO_GIT_CHECKPOINTS", False, backup)
        _set_config("ALLOW_EMPTY_DONE_RETRY", False, backup)

        agent_loop.call_model = fake_call_model
        prompt = "Inspect workspace files and report findings only. Do not modify files."
        agent_loop.run(prompt, logger=lambda msg: logs.append(str(msg)))

        assert any("TASK INTENT: inspect_only" in line for line in logs), "inspect-only intent should be detected"
        assert any("TASK SHAPE: analysis_report_task" in line for line in logs), "inspect-only shape should be analysis_report_task"
        assert not any(line.startswith("Wrote ") for line in logs), "inspect-only run must not write files"
        assert any("DONE" == line.strip() for line in logs), "inspect-only run should finish with done"
        assert any("RUN SUMMARY:" in line and "status=success" in line for line in logs), "inspect-only run should stop cleanly"
        assert call_count["value"] == 2, f"unexpected iteration count: {call_count['value']}"
    finally:
        agent_loop.call_model = original_call_model
        for key, value in backup.items():
            setattr(config, key, value)
        shutil.rmtree(root, ignore_errors=True)

    print("OK: inspect-only no-write stop regression checks passed")


if __name__ == "__main__":
    main()

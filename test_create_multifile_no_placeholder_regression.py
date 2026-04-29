import os
import shutil
import uuid

import agent_loop
import config


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def main():
    root = os.path.join(os.getcwd(), "TEST", f"multifile_no_placeholder_{uuid.uuid4().hex[:8]}")
    os.makedirs(root, exist_ok=True)
    source = os.path.join(root, "source.txt")
    with open(source, "w", encoding="utf-8") as handle:
        handle.write("source for multi-file transform\n")

    logs = []
    config_backup = {}
    original_call_model = agent_loop.call_model
    call_count = {"value": 0}

    responses = [
        (
            '{"actions":['
            '{"type":"write_file","args":{"path":"math_utils.py","content":"def add(a, b):\\n    return a + b\\n"}},'
            '{"type":"write_file","args":{"path":"main.py","content":"from math_utils import add\\nprint(add(2, 3))\\n"}}'
            "]}""\n"
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
        _set_config("PROJECT_ROOT", root, config_backup)
        _set_config("SESSION_FILE", ".agent/session_multifile_no_placeholder.json", config_backup)
        _set_config("PATCH_FILES", "", config_backup)
        _set_config("MODE_CONTROL", "AUTO", config_backup)
        _set_config("RESCUE_MODE", "OFF", config_backup)
        _set_config("MAX_ITERATIONS", 8, config_backup)
        _set_config("AUTO_VERIFY_PYTHON", True, config_backup)
        _set_config("AUTO_GIT_CHECKPOINTS", False, config_backup)
        _set_config("ALLOW_EMPTY_DONE_RETRY", False, config_backup)

        agent_loop.call_model = fake_call_model

        prompt = "transform and copy source.txt to create main.py and math_utils.py"
        agent_loop.run(prompt, logger=lambda msg: logs.append(str(msg)))

        assert os.path.exists(os.path.join(root, "main.py")), "main.py missing"
        assert os.path.exists(os.path.join(root, "math_utils.py")), "math_utils.py missing"
        assert not any("no placeholder markers detected" in line for line in logs), "legacy placeholder failure should not appear"
        assert any("TRANSFORM VERIFY OK" in line for line in logs), "transform verify should pass"
        assert any("RUN SUMMARY:" in line and "status=success" in line for line in logs), "run should finish successfully"
        assert call_count["value"] <= 2, f"unexpected retry loop count: {call_count['value']}"
    finally:
        agent_loop.call_model = original_call_model
        for key, value in config_backup.items():
            setattr(config, key, value)
        shutil.rmtree(root, ignore_errors=True)

    print("OK: multi-file no-placeholder regression checks passed")


if __name__ == "__main__":
    main()

import os
import shutil
import uuid

import agent_loop
import config


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def main():
    root = os.path.join(os.getcwd(), "TEST", f"standard_create_noop_stop_{uuid.uuid4().hex[:8]}")
    os.makedirs(root, exist_ok=True)

    logs = []
    backup = {}
    original_call_model = agent_loop.call_model
    call_count = {"value": 0}

    responses = [
        '{"actions":[{"type":"write_file","args":{"path":"calc_app.py","content":"x = 4\\ny = 5\\nprint(x + y)\\n"}}]}',
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
        _set_config("SESSION_FILE", ".agent/session_standard_create_noop_stop.json", backup)
        _set_config("PATCH_FILES", "", backup)
        _set_config("MODE_CONTROL", "AUTO", backup)
        _set_config("RESCUE_MODE", "OFF", backup)
        _set_config("MAX_ITERATIONS", 8, backup)
        _set_config("AUTO_VERIFY_PYTHON", True, backup)
        _set_config("AUTO_GIT_CHECKPOINTS", False, backup)
        _set_config("ALLOW_EMPTY_DONE_RETRY", False, backup)

        agent_loop.call_model = fake_call_model

        # Keep prompt intentionally long to route into standard_create profile.
        # Include extra token-like verifier path to mimic legacy target over-derivation pressure.
        prompt = (
            "Create calc_app.py that asks for two numbers and prints their sum. "
            "Use simple clear Python and preserve readability. "
            "After writing, verify with python -m py_compile calc_app.py and follow the verifier hint "
            "tools/verify_helper.py (verification helper path only, do not create it). "
            "Do not add extra files, keep only the requested calculator script."
        )
        agent_loop.run(prompt, logger=lambda msg: logs.append(str(msg)))

        output_path = os.path.join(root, "calc_app.py")
        assert os.path.exists(output_path), "calc_app.py missing"
        assert any("TASK PROFILE: standard_create" in line for line in logs), "test did not route to standard_create"
        assert any("VERIFY OK: calc_app.py" in line for line in logs), "python verify should pass"
        assert any("SKIP SAME FILE calc_app.py" in line for line in logs), "expected repeated identical write/no-op signal"
        assert any("STANDARD CREATE VERIFIED NO-OP -> STOP" in line for line in logs), "should stop after verified no-op"
        assert any("RUN SUMMARY:" in line and "status=success" in line for line in logs), "run should finish successfully"
        assert call_count["value"] == 2, f"unexpected iteration count: {call_count['value']}"
    finally:
        agent_loop.call_model = original_call_model
        for key, value in backup.items():
            setattr(config, key, value)
        shutil.rmtree(root, ignore_errors=True)

    print("OK: standard_create python noop stop regression checks passed")


if __name__ == "__main__":
    main()

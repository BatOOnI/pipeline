import os
import shutil
import uuid

import agent_loop
import config


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def main():
    root = os.path.join(os.getcwd(), "TEST", f"calc_abs_verify_{uuid.uuid4().hex[:8]}")
    os.makedirs(root, exist_ok=True)
    source = os.path.join(root, "source.txt")
    with open(source, "w", encoding="utf-8") as handle:
        handle.write("simple source body\n")

    logs = []
    config_backup = {}
    original_call_model = agent_loop.call_model
    call_count = {"value": 0}

    responses = [
        '{"actions":[{"type":"write_file","args":{"path":"calc_app.py","content":"x = 1\\ny = 2\\nprint(x + y)\\n"}}]}',
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
        _set_config("SESSION_FILE", ".agent/session_calc_abs_verify.json", config_backup)
        _set_config("PATCH_FILES", "", config_backup)
        _set_config("MODE_CONTROL", "AUTO", config_backup)
        _set_config("RESCUE_MODE", "OFF", config_backup)
        _set_config("MAX_ITERATIONS", 8, config_backup)
        _set_config("AUTO_VERIFY_PYTHON", True, config_backup)
        _set_config("AUTO_GIT_CHECKPOINTS", False, config_backup)
        _set_config("ALLOW_EMPTY_DONE_RETRY", False, config_backup)

        agent_loop.call_model = fake_call_model

        prompt = (
            "transform and copy source.txt to create calc_app.py, then verify with "
            r"C:\Users\B\AppData\Local\Programs\Python\Python314\python.exe -m py_compile calc_app.py"
        )
        agent_loop.run(prompt, logger=lambda msg: logs.append(str(msg)))

        assert os.path.exists(os.path.join(root, "calc_app.py")), "calc_app.py missing"
        derived_lines = [line for line in logs if "DERIVED FILES ALLOWED:" in line]
        assert derived_lines, "missing derived output log line"
        assert any("calc_app.py" in line for line in derived_lines), "calc_app.py missing from derived outputs"
        assert all("python.exe" not in line.lower() for line in derived_lines), "python.exe leaked into derived outputs"
        assert not any("no placeholder markers detected" in line for line in logs), "legacy placeholder failure should not appear"
        assert any("TRANSFORM VERIFY OK" in line for line in logs), "transform verify should pass"
        assert any("RUN SUMMARY:" in line and "status=success" in line for line in logs), "run should finish successfully"
        assert call_count["value"] <= 2, f"unexpected retry loop count: {call_count['value']}"
    finally:
        agent_loop.call_model = original_call_model
        for key, value in config_backup.items():
            setattr(config, key, value)
        shutil.rmtree(root, ignore_errors=True)

    print("OK: calc_app absolute-python verify regression checks passed")


if __name__ == "__main__":
    main()

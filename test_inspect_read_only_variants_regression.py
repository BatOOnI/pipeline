import os
import shutil
import uuid

import agent_loop
import config


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def _run_case(prompt_text):
    root = os.path.join(os.getcwd(), "TEST", f"inspect_readonly_{uuid.uuid4().hex[:8]}")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "ATTC.txt"), "w", encoding="utf-8") as handle:
        handle.write("ATTC sample content.\n")

    logs = []
    backup = {}
    original_call_model = agent_loop.call_model
    call_count = {"value": 0}
    responses = [
        '{"actions":[{"type":"read_file","args":{"path":"ATTC.txt"}}]}',
        "The file is not empty and the text is coherent.",
    ]

    def fake_call_model(*args, **kwargs):
        idx = call_count["value"]
        call_count["value"] += 1
        if idx < len(responses):
            return responses[idx]
        return "done"

    try:
        _set_config("PROJECT_ROOT", root, backup)
        _set_config("SESSION_FILE", ".agent/session_inspect_readonly_variants.json", backup)
        _set_config("PATCH_FILES", "", backup)
        _set_config("MODE_CONTROL", "AUTO", backup)
        _set_config("RESCUE_MODE", "OFF", backup)
        _set_config("MAX_ITERATIONS", 6, backup)
        _set_config("AUTO_VERIFY_PYTHON", False, backup)
        _set_config("AUTO_GIT_CHECKPOINTS", False, backup)
        _set_config("ALLOW_EMPTY_DONE_RETRY", False, backup)

        agent_loop.call_model = fake_call_model
        agent_loop.run(prompt_text, logger=lambda msg: logs.append(str(msg)))

        assert any("TASK INTENT: inspect_only" in line for line in logs), "inspect-only intent expected"
        assert any("PATCH HEURISTICS: disabled" in line for line in logs), "patch heuristics should be disabled"
        assert not any("ACTIVE PATCH TARGET:" in line for line in logs), "must not force patch target in read-only task"
        assert not any("PATCH HOTSPOT" in line for line in logs), "patch hotspots must not appear"
        assert not any("PATCH ACTION BIAS" in line for line in logs), "patch bias must not appear"
        assert not any(line.startswith("Wrote ") for line in logs), "read-only task must not write files"
        assert any("FINAL ANSWER BEGIN" in line for line in logs), "final answer block should be visible"
        assert any("FINAL ANSWER: The file is not empty and the text is coherent." in line for line in logs), (
            "final answer text should be surfaced in log"
        )
        assert any("NO CHANGES MADE (READ-ONLY TASK)" in line for line in logs), "read-only status should be explicit"
        assert any("RUN SUMMARY:" in line and "status=success" in line for line in logs), "run should finish successfully"
        assert call_count["value"] == 2, f"unexpected iteration count: {call_count['value']}"
    finally:
        agent_loop.call_model = original_call_model
        for key, value in backup.items():
            setattr(config, key, value)
        shutil.rmtree(root, ignore_errors=True)


def main():
    prompts = [
        "check if ATTC.txt file has sense. do not change this file. read only!",
        "check if ATTC.txt file has sense. do not modify this file. read only!",
        "analyze ATTC.txt only, no edits.",
    ]
    for prompt in prompts:
        _run_case(prompt)
    print("OK: inspect/read-only variants regression checks passed")


if __name__ == "__main__":
    main()

import json
import os
import shutil
import uuid

import agent_loop
import config


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def _run_case(prompt_text, responses, create_attc=False):
    root = os.path.join(os.getcwd(), "TEST", f"answer_action_{uuid.uuid4().hex[:8]}")
    os.makedirs(root, exist_ok=True)
    if create_attc:
        with open(os.path.join(root, "ATTC.txt"), "w", encoding="utf-8") as handle:
            handle.write("ATTC sample content.\n")

    logs = []
    backup = {}
    original_call_model = agent_loop.call_model
    call_count = {"value": 0}

    def fake_call_model(*args, **kwargs):
        idx = call_count["value"]
        call_count["value"] += 1
        if idx < len(responses):
            return responses[idx]
        return "done"

    try:
        _set_config("PROJECT_ROOT", root, backup)
        _set_config("SESSION_FILE", f".agent/session_answer_action_{uuid.uuid4().hex}.json", backup)
        _set_config("PATCH_FILES", "", backup)
        _set_config("MODE_CONTROL", "AUTO", backup)
        _set_config("RESCUE_MODE", "OFF", backup)
        _set_config("MAX_ITERATIONS", 6, backup)
        _set_config("AUTO_VERIFY_PYTHON", False, backup)
        _set_config("AUTO_GIT_CHECKPOINTS", False, backup)
        _set_config("ALLOW_EMPTY_DONE_RETRY", True, backup)

        agent_loop.call_model = fake_call_model
        agent_loop.run(prompt_text, logger=lambda msg: logs.append(str(msg)))
        return logs, call_count["value"], root
    finally:
        agent_loop.call_model = original_call_model
        for key, value in backup.items():
            setattr(config, key, value)
        shutil.rmtree(root, ignore_errors=True)


def test_answer_only_action_stops_successfully():
    prompt = "nie twórz plików. odpowiedz tylko czy umiesz skonfigurować X"
    response = json.dumps(
        {
            "plan": "answer user directly",
            "reasoning_short": "direct answer",
            "actions": [{"type": "answer", "args": {"text": "Tak, umiem skonfigurować X."}}],
        }
    )
    logs, call_count, _root = _run_case(prompt, [response], create_attc=False)

    assert any("FINAL ANSWER: Tak, umiem skonfigurować X." in line for line in logs), "answer action should be surfaced"
    assert any("RUN SUMMARY:" in line and "status=success" in line for line in logs), "answer-only run should finish as success"
    assert any("No files were modified." in line for line in logs), "no file modifications should be explicit"
    assert not any(line.startswith("Wrote ") for line in logs), "answer-only run must not write files"
    assert not any("EMPTY DONE TOO EARLY" in line for line in logs), "answer action must avoid empty-done retry path"
    assert call_count == 1, f"answer-only run should stop immediately, got {call_count} turns"


def test_inspect_read_then_answer_action_stops_successfully():
    prompt = "check if ATTC.txt file has sense. do not change this file. read only!"
    responses = [
        json.dumps({"actions": [{"type": "read_file", "args": {"path": "ATTC.txt"}}]}),
        json.dumps(
            {
                "plan": "report findings",
                "reasoning_short": "summarize findings",
                "actions": [
                    {
                        "type": "answer",
                        "args": {"text": "Classification: high-risk/offensive cyber content."},
                    }
                ],
            }
        ),
    ]
    logs, call_count, _root = _run_case(prompt, responses, create_attc=True)

    assert any("read_file ATTC.txt" in line for line in logs), "inspect flow should read file first"
    assert any("FINAL ANSWER: Classification: high-risk/offensive cyber content." in line for line in logs), (
        "answer action findings should be visible"
    )
    assert any("RUN SUMMARY:" in line and "status=success" in line for line in logs), "inspect answer run should finish as success"
    assert not any(line.startswith("Wrote ") for line in logs), "inspect answer run must not write files"
    assert call_count == 2, f"expected read + answer turns, got {call_count}"


def test_actions_empty_reasoning_short_fallback_still_works():
    prompt = "Analyze ATTC.txt only, no edits."
    responses = [
        json.dumps({"actions": [{"type": "read_file", "args": {"path": "ATTC.txt"}}]}),
        json.dumps(
            {
                "plan": "Inspect ATTC.txt and summarize findings",
                "reasoning_short": "The file contains a penetration testing plan with risky offensive steps.",
                "actions": [],
            }
        ),
    ]
    logs, call_count, _root = _run_case(prompt, responses, create_attc=True)

    assert any("FINAL ANSWER: The file contains a penetration testing plan with risky offensive steps." in line for line in logs), (
        "actions:[] fallback with meaningful reasoning_short should remain supported"
    )
    assert any("RUN SUMMARY:" in line and "status=success" in line for line in logs), "fallback path should finish successfully"
    assert call_count == 2, f"expected read + fallback answer turns, got {call_count}"


def main():
    test_answer_only_action_stops_successfully()
    test_inspect_read_then_answer_action_stops_successfully()
    test_actions_empty_reasoning_short_fallback_still_works()
    print("OK: answer action regression checks passed")


if __name__ == "__main__":
    main()


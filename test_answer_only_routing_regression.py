import json
import os
import shutil
import uuid

import agent_loop
import config


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def _run_case(prompt_text, responses):
    root = os.path.join(os.getcwd(), "TEST", f"answer_only_route_{uuid.uuid4().hex[:8]}")
    os.makedirs(root, exist_ok=True)

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
        _set_config("SESSION_FILE", f".agent/session_answer_only_route_{uuid.uuid4().hex}.json", backup)
        _set_config("PATCH_FILES", "", backup)
        _set_config("MODE_CONTROL", "AUTO", backup)
        _set_config("RESCUE_MODE", "OFF", backup)
        _set_config("MAX_ITERATIONS", 5, backup)
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


def test_polish_answer_only_routes_without_create_bias():
    prompt = "nie twórz plików. odpowiedz tylko czy umiesz skonfigurować X"
    response = json.dumps(
        {
            "plan": "answer user",
            "reasoning_short": "direct answer",
            "actions": [{"type": "answer", "args": {"text": "Tak, umiem skonfigurowac X."}}],
        }
    )
    logs, call_count, _root = _run_case(prompt, [response])

    assert any("TASK INTENT: answer_only" in line for line in logs), "should route to answer_only intent"
    assert not any("TASK PROFILE: simple_create" in line for line in logs), "answer-only should not use simple_create profile"
    assert not any("CREATE STRATEGY: write_file" in line for line in logs), "answer-only should not bias write_file strategy"
    assert any("FINAL ANSWER: Tak, umiem skonfigurowac X." in line for line in logs), "final answer should be visible"
    assert not any(line.startswith("Wrote ") for line in logs), "answer-only should not write files"
    assert any("RUN SUMMARY:" in line and "status=success" in line for line in logs), "answer-only should finish successfully"
    assert call_count == 1, f"expected single answer turn, got {call_count}"


def test_english_answer_only_routes_without_create_bias():
    prompt = "Do not create files. Answer only: can you configure X?"
    response = json.dumps(
        {
            "plan": "answer user",
            "reasoning_short": "direct answer",
            "actions": [{"type": "final_answer", "args": {"text": "Yes, I can configure X."}}],
        }
    )
    logs, call_count, _root = _run_case(prompt, [response])

    assert any("TASK INTENT: answer_only" in line for line in logs), "should route to answer_only intent"
    assert not any("TASK PROFILE: simple_create" in line for line in logs), "answer-only should not use simple_create profile"
    assert not any("CREATE STRATEGY: write_file" in line for line in logs), "answer-only should not bias write_file strategy"
    assert any("FINAL ANSWER: Yes, I can configure X." in line for line in logs), "final answer should be visible"
    assert not any(line.startswith("Wrote ") for line in logs), "answer-only should not write files"
    assert any("RUN SUMMARY:" in line and "status=success" in line for line in logs), "answer-only should finish successfully"
    assert call_count == 1, f"expected single answer turn, got {call_count}"


def test_real_create_still_routes_create_new_file():
    prompt = "Create app.py that prints hello"
    response = json.dumps(
        {
            "actions": [{"type": "write_file", "args": {"path": "app.py", "content": "print('hello')\n"}}],
        }
    )
    logs, call_count, _root = _run_case(prompt, [response])

    assert any("TASK INTENT: create_new_file" in line for line in logs), "real create prompt should still route to create_new_file"
    assert any("Wrote app.py" in line for line in logs), "create task should write file"
    assert any("RUN SUMMARY:" in line and "status=success" in line for line in logs), "create task should finish successfully"
    assert call_count == 1, f"expected single create turn, got {call_count}"


def main():
    test_polish_answer_only_routes_without_create_bias()
    test_english_answer_only_routes_without_create_bias()
    test_real_create_still_routes_create_new_file()
    print("OK: answer-only routing regression checks passed")


if __name__ == "__main__":
    main()


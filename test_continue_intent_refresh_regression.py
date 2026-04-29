import json
import os
import shutil
import uuid

import agent_loop
import config


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def _run_case(prompt_text, responses, continue_update="", root=None, session_file=None):
    if not root:
        root = os.path.join(os.getcwd(), "TEST", f"continue_intent_{uuid.uuid4().hex[:8]}")
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
        _set_config("SESSION_FILE", session_file or f".agent/session_continue_intent_{uuid.uuid4().hex}.json", backup)
        _set_config("PATCH_FILES", "", backup)
        _set_config("MODE_CONTROL", "AUTO", backup)
        _set_config("RESCUE_MODE", "OFF", backup)
        _set_config("MAX_ITERATIONS", 4, backup)
        _set_config("AUTO_VERIFY_PYTHON", False, backup)
        _set_config("AUTO_GIT_CHECKPOINTS", False, backup)
        _set_config("ALLOW_EMPTY_DONE_RETRY", True, backup)

        agent_loop.call_model = fake_call_model
        agent_loop.run(prompt_text, continue_update=continue_update, logger=lambda msg: logs.append(str(msg)))
        return logs, call_count["value"], root
    finally:
        agent_loop.call_model = original_call_model
        for key, value in backup.items():
            setattr(config, key, value)


def test_initial_answer_only_routes_as_answer_only():
    prompt = "nie tworz plikow. odpowiedz tylko czy umiesz skonfigurowac X"
    response = json.dumps(
        {
            "actions": [{"type": "answer", "args": {"text": "Tak, umiem."}}],
        }
    )
    logs, call_count, root = _run_case(prompt, [response])
    try:
        assert any("TASK INTENT: answer_only" in line for line in logs), "initial prompt should route to answer_only"
        assert call_count == 1, f"expected single response turn, got {call_count}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_continue_refreshes_answer_only_to_execution_intent():
    base_prompt = "nie tworz plikow. odpowiedz tylko czy umiesz skonfigurowac X"
    followup = "to pobierz i skonfiguruj pliki sam. zrob to w folderze TEST"
    session_file = f".agent/session_continue_intent_{uuid.uuid4().hex}.json"
    root = os.path.join(os.getcwd(), "TEST", f"continue_intent_{uuid.uuid4().hex[:8]}")

    first_response = json.dumps({"actions": [{"type": "answer", "args": {"text": "Tak."}}]})
    second_response = json.dumps(
        {"actions": [{"type": "write_file", "args": {"path": "setup.txt", "content": "configured\n"}}]}
    )

    try:
        logs1, _, _ = _run_case(base_prompt, [first_response], root=root, session_file=session_file)
        assert any("TASK INTENT: answer_only" in line for line in logs1), "base prompt should route to answer_only"

        logs2, call_count, _ = _run_case(
            base_prompt,
            [second_response],
            continue_update=followup,
            root=root,
            session_file=session_file,
        )
        assert any("CONTINUE MODE: ON" in line for line in logs2), "continue run should be marked"
        assert any("CONTINUE INTENT REFRESH:" in line for line in logs2), "continue run should log intent refresh"
        assert not any("TASK INTENT: answer_only" in line for line in logs2), "execution follow-up must not stay answer_only"
        assert any("Wrote setup.txt" in line for line in logs2), "execution follow-up should allow file action"
        assert call_count >= 1, f"continue run should call model at least once, got {call_count}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_continue_answer_only_followup_can_stay_answer_only():
    base_prompt = "nie tworz plikow. odpowiedz tylko czy umiesz skonfigurowac X"
    followup = "ok, tylko wyjasnij ogolnie jak to dziala"
    session_file = f".agent/session_continue_intent_{uuid.uuid4().hex}.json"
    root = os.path.join(os.getcwd(), "TEST", f"continue_intent_{uuid.uuid4().hex[:8]}")

    first_response = json.dumps({"actions": [{"type": "answer", "args": {"text": "Tak."}}]})
    second_response = json.dumps({"actions": [{"type": "answer", "args": {"text": "Dziala tak: ..."}}]})

    try:
        _run_case(base_prompt, [first_response], root=root, session_file=session_file)
        logs2, _, _ = _run_case(
            base_prompt,
            [second_response],
            continue_update=followup,
            root=root,
            session_file=session_file,
        )
        assert any("TASK INTENT: answer_only" in line for line in logs2), "answer-only follow-up should remain answer_only"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_generic_continue_preserves_prior_intent():
    base_prompt = "nie tworz plikow. odpowiedz tylko czy umiesz skonfigurowac X"
    session_file = f".agent/session_continue_intent_{uuid.uuid4().hex}.json"
    root = os.path.join(os.getcwd(), "TEST", f"continue_intent_{uuid.uuid4().hex[:8]}")

    first_response = json.dumps({"actions": [{"type": "answer", "args": {"text": "Tak."}}]})
    second_response = json.dumps({"actions": [{"type": "answer", "args": {"text": "continuing"}}]})

    try:
        _run_case(base_prompt, [first_response], root=root, session_file=session_file)
        logs2, _, _ = _run_case(
            base_prompt,
            [second_response],
            continue_update="continue",
            root=root,
            session_file=session_file,
        )
        assert any("TASK INTENT: answer_only" in line for line in logs2), "generic continue should preserve prior intent"
        assert not any("CONTINUE INTENT REFRESH:" in line for line in logs2), "generic continue should not force intent refresh"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    test_initial_answer_only_routes_as_answer_only()
    test_continue_refreshes_answer_only_to_execution_intent()
    test_continue_answer_only_followup_can_stay_answer_only()
    test_generic_continue_preserves_prior_intent()
    print("OK: continue intent refresh regression checks passed")


if __name__ == "__main__":
    main()

import json
import os
import shutil
import uuid

import agent_loop
import config


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def _run_case(prompt_text, responses, continue_update="", root=None, session_file=None, permission_mode="workspace-write"):
    if not root:
        root = os.path.join(os.getcwd(), "TEST", f"exec_route_{uuid.uuid4().hex[:8]}")
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
        _set_config("SESSION_FILE", session_file or f".agent/session_exec_route_{uuid.uuid4().hex}.json", backup)
        _set_config("PATCH_FILES", "", backup)
        _set_config("MODE_CONTROL", "AUTO", backup)
        _set_config("RESCUE_MODE", "OFF", backup)
        _set_config("MAX_ITERATIONS", 4, backup)
        _set_config("AUTO_VERIFY_PYTHON", False, backup)
        _set_config("AUTO_GIT_CHECKPOINTS", False, backup)
        _set_config("ALLOW_EMPTY_DONE_RETRY", False, backup)
        _set_config("PERMISSION_MODE", permission_mode, backup)
        _set_config("PERMISSION_ALLOW_RULES", "", backup)
        _set_config("PERMISSION_DENY_RULES", "", backup)
        _set_config("PERMISSION_ASK_RULES", "", backup)
        _set_config("NETWORK_ENABLED", True, backup)

        agent_loop.call_model = fake_call_model
        agent_loop.run(prompt_text, continue_update=continue_update, logger=lambda msg: logs.append(str(msg)))
        return logs, call_count["value"], root
    finally:
        agent_loop.call_model = original_call_model
        for key, value in backup.items():
            setattr(config, key, value)


def test_continue_from_answer_only_refreshes_to_run_command_intent():
    base_prompt = "nie tworz plikow. odpowiedz tylko czy umiesz skonfigurowac X"
    followup = "to pobierz i skonfiguruj pliki sam. zrob to w folderze TEST"
    session_file = f".agent/session_exec_route_{uuid.uuid4().hex}.json"
    root = os.path.join(os.getcwd(), "TEST", f"exec_route_{uuid.uuid4().hex[:8]}")

    first_response = json.dumps({"actions": [{"type": "answer", "args": {"text": "Tak."}}]})
    second_response = json.dumps({"actions": [{"type": "run_cmd", "args": {"cmd": "echo setup"}}]})

    try:
        logs1, _, _ = _run_case(base_prompt, [first_response], root=root, session_file=session_file)
        assert any("TASK INTENT: answer_only" in line for line in logs1), "base prompt should route to answer_only"

        logs2, _, _ = _run_case(
            base_prompt,
            [second_response, "done"],
            continue_update=followup,
            root=root,
            session_file=session_file,
        )
        assert any("CONTINUE INTENT REFRESH:" in line for line in logs2), "intent refresh should be logged"
        assert any("TASK INTENT: run_command_only" in line for line in logs2), "execution follow-up should route to run_command_only"
        assert not any("TASK INTENT: answer_only" in line for line in logs2), "execution follow-up must not stay answer_only"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_neutral_download_routes_run_command_and_hits_permission_gate():
    prompt = "pobierz plik testowy z https://example.com do folderu TEST za pomocą PowerShell"
    response = json.dumps(
        {
            "actions": [
                {
                    "type": "run_cmd",
                    "args": {
                        "cmd": "powershell -NoProfile -Command \"Invoke-WebRequest -Uri https://example.com -OutFile TEST\\sample.txt\""
                    },
                }
            ]
        }
    )
    logs, _count, root = _run_case(prompt, [response, "done"], permission_mode="prompt")
    try:
        assert any("TASK INTENT: run_command_only" in line for line in logs), "download/setup prompt should route to run_command_only"
        assert any("PERMISSION DENIED" in line for line in logs), "run_cmd should hit permission gate in prompt mode"
        assert any("Mode prompt requires confirmation for each action." in line for line in logs), "prompt-mode confirmation reason expected"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_answer_action_is_not_permission_prompted():
    prompt = "nie tworz plikow. odpowiedz tylko czy umiesz skonfigurowac X"
    response = json.dumps({"actions": [{"type": "answer", "args": {"text": "Tak, moge."}}]})
    logs, _count, root = _run_case(prompt, [response], permission_mode="prompt")
    try:
        assert any("FINAL ANSWER: Tak, moge." in line for line in logs), "answer action should be surfaced"
        assert not any("PERMISSION DENIED" in line for line in logs), "answer action must not trigger permission denial/prompt loop"
        assert any("RUN SUMMARY:" in line and "status=success" in line for line in logs), "answer action should finish successfully"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_real_create_still_routes_create_new_file():
    prompt = "stworz plik app.py"
    response = json.dumps({"actions": [{"type": "write_file", "args": {"path": "app.py", "content": "print('ok')\n"}}]})
    logs, _count, root = _run_case(prompt, [response])
    try:
        assert any("TASK INTENT: create_new_file" in line for line in logs), "real create should stay create_new_file"
        assert any("Wrote app.py" in line for line in logs), "create should write file"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    test_continue_from_answer_only_refreshes_to_run_command_intent()
    test_neutral_download_routes_run_command_and_hits_permission_gate()
    test_answer_action_is_not_permission_prompted()
    test_real_create_still_routes_create_new_file()
    print("OK: execution routing and answer permission regression checks passed")


if __name__ == "__main__":
    main()

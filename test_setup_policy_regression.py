import json
import os
import shutil
import uuid

import agent_loop
import config
from contracts import Observation
from executor import find_in_file


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def _run_case(prompt_text, responses, root=None, patch_execute=None):
    if not root:
        root = os.path.join(os.getcwd(), "TEST", f"setup_policy_{uuid.uuid4().hex[:8]}")
    os.makedirs(root, exist_ok=True)
    logs = []
    backup = {}
    original_call_model = agent_loop.call_model
    original_execute = agent_loop._execute_action
    call_count = {"value": 0}

    def fake_call_model(*args, **kwargs):
        idx = call_count["value"]
        call_count["value"] += 1
        if idx < len(responses):
            return responses[idx]
        return "done"

    try:
        _set_config("PROJECT_ROOT", root, backup)
        _set_config("SESSION_FILE", f".agent/session_setup_policy_{uuid.uuid4().hex}.json", backup)
        _set_config("PATCH_FILES", "", backup)
        _set_config("MODE_CONTROL", "AUTO", backup)
        _set_config("RESCUE_MODE", "OFF", backup)
        _set_config("MAX_ITERATIONS", 8, backup)
        _set_config("AUTO_VERIFY_PYTHON", False, backup)
        _set_config("AUTO_GIT_CHECKPOINTS", False, backup)
        _set_config("ALLOW_EMPTY_DONE_RETRY", False, backup)
        _set_config("PERMISSION_MODE", "allow", backup)
        _set_config("PERMISSION_ALLOW_RULES", "", backup)
        _set_config("PERMISSION_DENY_RULES", "", backup)
        _set_config("PERMISSION_ASK_RULES", "", backup)
        _set_config("NETWORK_ENABLED", True, backup)
        _set_config("SETUP_MAX_SIDE_EFFECT_ACTIONS", 6, backup)
        _set_config("SETUP_BLOCKED_COMMAND_REPEAT_LIMIT", 2, backup)

        agent_loop.call_model = fake_call_model
        if callable(patch_execute):
            agent_loop._execute_action = patch_execute
        agent_loop.run(prompt_text, logger=lambda msg: logs.append(str(msg)))
        return logs, call_count["value"], root
    finally:
        agent_loop.call_model = original_call_model
        agent_loop._execute_action = original_execute
        for key, value in backup.items():
            setattr(config, key, value)


def test_shell_operator_blocked_one_recovery_then_blocked():
    prompt = "pobierz i skonfiguruj narzedzie w folderze TEST, uzyj komend"
    blocked_cmd = json.dumps({"actions": [{"type": "run_cmd", "args": {"cmd": 'cd /d "TEST" && dir'}}]})
    logs, calls, root = _run_case(prompt, [blocked_cmd, blocked_cmd, "done"])
    try:
        assert any("SETUP RECOVERY: shell_operator_blocked" in line for line in logs), "expected shell-operator recovery"
        assert any("SETUP BLOCKED: repeated shell operator command block" in line for line in logs), "expected bounded stop on repeated shell operator block"
        assert any("RUN SUMMARY:" in line and "status=blocked" in line for line in logs), "expected blocked status summary"
        assert calls <= 3, "should not loop unbounded"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_answer_mixed_with_tools_one_retry_then_blocked():
    prompt = "pobierz plik testowy i skonfiguruj, potem odpowiedz"
    mixed = json.dumps(
        {
            "actions": [
                {"type": "answer", "args": {"text": "zrobione"}},
                {"type": "run_cmd", "args": {"cmd": ["python", "-c", "print('ok')"]}},
            ]
        }
    )
    logs, calls, root = _run_case(prompt, [mixed, mixed, "done"])
    try:
        assert any("SETUP RECOVERY: answer_mixed_with_tools" in line for line in logs), "expected mixed-answer recovery"
        assert any("SETUP BLOCKED: repeated answer+tools contract violation" in line for line in logs), "expected bounded stop on repeated violation"
        assert any("RUN SUMMARY:" in line and "status=blocked" in line for line in logs), "expected blocked status summary"
        assert calls <= 3, "should not loop unbounded"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_repeated_same_blocked_command_stops():
    prompt = "uruchom komende testowa"
    blocked = json.dumps({"actions": [{"type": "run_cmd", "args": {"cmd": r"python \B"}}]})
    logs, calls, root = _run_case(prompt, [blocked, blocked, blocked, "done"])
    try:
        assert any("SETUP BLOCKED: repeated same command blocked" in line for line in logs), "expected repeated-blocked stop"
        assert any("RUN SUMMARY:" in line and "status=blocked" in line for line in logs), "expected blocked status summary"
        assert calls <= 4, "should not loop unbounded"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_too_many_actions_are_bounded():
    prompt = "pobierz i skonfiguruj pakiet krok po kroku"
    actions = [{"type": "run_cmd", "args": {"cmd": ["python", "-c", f"print('ok{i}')"]}} for i in range(12)]
    response = json.dumps({"actions": actions})
    logs, _calls, root = _run_case(prompt, [response])
    try:
        assert any("SETUP ACTION BUDGET: truncated side-effect actions 12 -> 6" in line for line in logs), "expected action budget truncation"
        assert any("RUN SUMMARY:" in line and "status=success" in line for line in logs), "bounded setup should still complete"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_partial_setup_after_download_then_failed_verify():
    prompt = "pobierz paczke i rozpakuj, potem skonfiguruj"
    response = json.dumps(
        {
            "actions": [
                {"type": "download_file", "args": {"url": "https://example.com/pkg.zip", "path": "pkg.zip"}},
                {"type": "run_cmd", "args": {"cmd": ["python", "-c", "raise SystemExit(1)"]}},
            ]
        }
    )

    def fake_execute(action, state, project_root_override=None, stop_checker=None, permission_decider=None):
        action_type = str(action.get("type", ""))
        root = project_root_override or state.active_project_root
        if action_type == "download_file":
            out = os.path.join(root, "pkg.zip")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as handle:
                handle.write(b"zip")
            return Observation(
                True,
                "Downloaded pkg.zip",
                changed=True,
                details="simulated download",
                tool="download_file",
                path=out,
                metadata={"touches_file": True},
            )
        if action_type == "run_cmd":
            return Observation(
                False,
                "CMD FAILED: ['python', '-c', 'raise SystemExit(1)']",
                changed=False,
                details="simulated setup verify failure",
                tool="run_cmd",
            )
        return Observation(True, f"{action_type} ok", changed=False, tool=action_type)

    logs, calls, root = _run_case(prompt, [response, response, "done"], patch_execute=fake_execute)
    try:
        assert any("SETUP RECOVERY: missing_expected_file_or_folder -> folder inspection" in line for line in logs), "expected one folder inspection recovery"
        assert any("SETUP PARTIAL SUMMARY:" in line for line in logs), "expected partial summary block"
        assert any("- completed steps:" in line for line in logs), "partial summary should include completed steps"
        assert any("- optional missing files:" in line for line in logs), "partial summary should include optional missing files section"
        assert any("- real blockers:" in line for line in logs), "partial summary should include real blockers"
        assert any("RUN SUMMARY:" in line and "status=partial" in line for line in logs), "expected partial status"
        assert calls <= 3, "should not loop unbounded after partial stop"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_optional_requirements_missing_not_fatal_when_pyproject_exists():
    prompt = "uruchom setup komendami: sprawdz pliki zaleznosci i skonfiguruj repo"
    response = json.dumps(
        {
            "actions": [
                {"type": "read_file", "args": {"path": "pyproject.toml"}},
                {"type": "read_file", "args": {"path": "setup.cfg"}},
                {"type": "read_file", "args": {"path": "requirements.txt"}},
                {"type": "run_cmd", "args": {"cmd": ["python", "-c", "print('ok')"]}},
            ]
        }
    )

    def fake_execute(action, state, project_root_override=None, stop_checker=None, permission_decider=None):
        action_type = str(action.get("type", ""))
        args = action.get("args", {}) if isinstance(action.get("args"), dict) else {}
        path = str(args.get("path", ""))
        if action_type == "read_file" and path == "pyproject.toml":
            return Observation(True, "Read pyproject.toml", changed=False, details="[build-system]\n...", tool="read_file", path=path)
        if action_type == "read_file" and path == "setup.cfg":
            return Observation(True, "Read setup.cfg", changed=False, details="[metadata]\n...", tool="read_file", path=path)
        if action_type == "read_file" and path == "requirements.txt":
            return Observation(False, "read_file failed requirements.txt", changed=False, details="404 Not Found", tool="read_file", path=path)
        if action_type == "run_cmd":
            return Observation(True, "CMD exit=0: ['python', '-c', \"print('ok')\"]", changed=False, details="ok", tool="run_cmd")
        return Observation(True, f"{action_type} ok", changed=False, tool=action_type)

    logs, _calls, root = _run_case(prompt, [response, "done"], patch_execute=fake_execute)
    try:
        assert any("SETUP OPTIONAL MISSING: requirements.txt" in line for line in logs), "optional missing requirements should be logged"
        assert not any("SETUP BLOCKED: repeated same command blocked" in line for line in logs), "optional missing file should not become repeated blocked command"
        assert any("RUN SUMMARY:" in line and "status=success" in line for line in logs), "setup should still complete successfully"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_find_in_file_directory_validation_error():
    root = os.path.join(os.getcwd(), "TEST", f"find_dir_{uuid.uuid4().hex[:8]}")
    os.makedirs(root, exist_ok=True)
    try:
        obs = find_in_file(".", "test", project_root=root)
        assert not obs.ok, "directory lookup should fail cleanly"
        assert "find_in_file requires a file path, got directory" in str(
            obs.details or ""
        ), "expected clean directory validation message"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_normal_run_cmd_success_still_works():
    prompt = "uruchom bezpieczna komende testowa i zakoncz"
    response = json.dumps({"actions": [{"type": "run_cmd", "args": {"cmd": ["python", "-c", "print('ok')"]}}]})
    logs, _calls, root = _run_case(prompt, [response])
    try:
        assert any("CMD exit=0" in line for line in logs), "normal run_cmd success should still work"
        assert any("RUN SUMMARY:" in line and "status=success" in line for line in logs), "run should finish successfully"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    test_shell_operator_blocked_one_recovery_then_blocked()
    test_answer_mixed_with_tools_one_retry_then_blocked()
    test_repeated_same_blocked_command_stops()
    test_too_many_actions_are_bounded()
    test_partial_setup_after_download_then_failed_verify()
    test_optional_requirements_missing_not_fatal_when_pyproject_exists()
    test_find_in_file_directory_validation_error()
    test_normal_run_cmd_success_still_works()
    print("OK: setup policy regression checks passed")


if __name__ == "__main__":
    main()

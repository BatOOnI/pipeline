import json
import os
import shutil
import uuid

import agent_loop
import config


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def _run_case(prompt_text, responses, allow_empty_done_retry=True):
    root = os.path.join(os.getcwd(), "TEST", f"inspect_answer_{uuid.uuid4().hex[:8]}")
    os.makedirs(root, exist_ok=True)
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
        _set_config("SESSION_FILE", f".agent/session_inspect_answer_surface_{uuid.uuid4().hex}.json", backup)
        _set_config("PATCH_FILES", "", backup)
        _set_config("MODE_CONTROL", "AUTO", backup)
        _set_config("RESCUE_MODE", "OFF", backup)
        _set_config("MAX_ITERATIONS", 6, backup)
        _set_config("AUTO_VERIFY_PYTHON", False, backup)
        _set_config("AUTO_GIT_CHECKPOINTS", False, backup)
        _set_config("ALLOW_EMPTY_DONE_RETRY", bool(allow_empty_done_retry), backup)

        agent_loop.call_model = fake_call_model
        agent_loop.run(prompt_text, logger=lambda msg: logs.append(str(msg)))
        return logs, call_count["value"]
    finally:
        agent_loop.call_model = original_call_model
        for key, value in backup.items():
            setattr(config, key, value)
        shutil.rmtree(root, ignore_errors=True)


def _assert_success(logs):
    assert any("RUN SUMMARY:" in line and "status=success" in line for line in logs), "run should finish successfully"


def test_inspect_json_prefers_reasoning_over_plan():
    prompt = "Check ATTC.txt. Do not change this file. Read-only analysis only."
    response = json.dumps(
        {
            "plan": "Inspect ATTC.txt for sensitive content without making changes",
            "reasoning_short": (
                "The file contains a penetration testing plan with sensitive instructions and offensive-security steps."
            ),
            "actions": [],
        }
    )
    logs, _ = _run_case(
        prompt,
        [
            '{"actions":[{"type":"read_file","args":{"path":"ATTC.txt"}}]}',
            response,
        ],
        allow_empty_done_retry=True,
    )
    _assert_success(logs)

    assert any("TASK INTENT: inspect_only" in line for line in logs), "inspect intent expected"
    assert any("read_file ATTC.txt" in line for line in logs), "inspect flow should read target file first"
    assert any("FINAL ANSWER BEGIN" in line for line in logs), "final answer block expected"
    assert any("FINAL ANSWER: The file contains a penetration testing plan" in line for line in logs), (
        "reasoning_short should be used as final answer"
    )
    assert not any(
        "FINAL ANSWER: Inspect ATTC.txt for sensitive content without making changes" in line for line in logs
    ), "plan text should not be chosen when reasoning_short is present"


def test_inspect_low_quality_answer_forces_one_more_turn():
    prompt = "Read ATTC.txt and assess whether content is risky. Do not modify. Read-only."
    responses = [
        '{"actions":[{"type":"read_file","args":{"path":"ATTC.txt"}}]}',
        json.dumps(
            {
                "plan": "Inspect ATTC.txt for sensitive content without making changes",
                "reasoning_short": (
                    "Need to analyze the ATTC.txt file to determine if it contains sensitive information "
                    "related to penetration testing, but must not modify the file."
                ),
                "actions": [],
            }
        ),
        json.dumps(
            {
                "plan": "Inspect complete",
                "reasoning_short": (
                    "Classification: high-risk/offensive cyber content. "
                    "The file contains a penetration testing plan with reconnaissance, exploitation, payload/persistence and cleanup steps."
                ),
                "actions": [],
            }
        ),
    ]
    logs, call_count = _run_case(prompt, responses, allow_empty_done_retry=True)
    _assert_success(logs)

    assert any("INSPECT QUALITY GATE: plan/intention answer rejected" in line for line in logs), (
        "quality gate should request one additional findings turn"
    )
    assert any("FINAL ANSWER: Classification: high-risk/offensive cyber content." in line for line in logs), (
        "meaningful findings should be surfaced"
    )
    assert call_count == 3, f"expected one additional turn, got {call_count}"


def test_inspect_no_read_evidence_forces_read_before_answer():
    prompt = "check if ATTC.txt file has sens. do not change this file. read only!"
    responses = [
        json.dumps(
            {
                "plan": "Inspect ATTC.txt for sensitive content",
                "reasoning_short": (
                    "The task requires only reading and analyzing the ATTC.txt file for sensitive information "
                    "without modifying it. This can be accomplished using read_file action."
                ),
                "actions": [],
            }
        ),
        '{"actions":[{"type":"read_file","args":{"path":"ATTC.txt"}}]}',
        json.dumps(
            {
                "plan": "Inspect complete",
                "reasoning_short": (
                    "Classification: high-risk/offensive cyber content. "
                    "The file contains a penetration testing plan with reconnaissance, exploitation, persistence and cleanup."
                ),
                "actions": [],
            }
        ),
    ]
    logs, call_count = _run_case(prompt, responses, allow_empty_done_retry=True)
    _assert_success(logs)
    assert any("INSPECT QUALITY GATE: no file inspection evidence -> read target first" in line for line in logs), (
        "runtime should reject final answer when no read_file/find_in_file evidence exists"
    )
    assert any("read_file ATTC.txt" in line for line in logs), "second turn should read the file"
    assert any("FINAL ANSWER: Classification: high-risk/offensive cyber content." in line for line in logs), (
        "final answer should appear only after read evidence"
    )
    assert call_count == 3, f"expected enforced read turn before final answer, got {call_count}"


def test_inspect_meta_analysis_answer_forces_one_more_turn():
    prompt = (
        "check if ATTC.txt file has sens. do not change this file. read only! "
        "does this plan is real?"
    )
    responses = [
        '{"actions":[{"type":"read_file","args":{"path":"ATTC.txt"}}]}',
        json.dumps(
            {
                "plan": "Inspect ATTC.txt file for sensitive content without making changes",
                "reasoning_short": (
                    "The task requires only reading and analyzing the ATTC.txt file to check for sensitive "
                    "information, with no modifications allowed. This is a static analysis task that can be "
                    "completed using read_file and find_in_file operations."
                ),
                "actions": [],
            }
        ),
        json.dumps(
            {
                "plan": "Inspect complete",
                "reasoning_short": (
                    "Classification: high-risk/offensive cyber content. "
                    "The file contains a penetration testing plan for a target VM/LAN with reconnaissance/scanning, "
                    "exploitation, payload/persistence and cleanup/deletion steps."
                ),
                "actions": [],
            }
        ),
    ]
    logs, call_count = _run_case(prompt, responses, allow_empty_done_retry=True)
    _assert_success(logs)
    assert any("INSPECT QUALITY GATE: plan/intention answer rejected" in line for line in logs), (
        "meta-analysis text should be rejected as low-quality final answer"
    )
    assert any("FINAL ANSWER: Classification: high-risk/offensive cyber content." in line for line in logs), (
        "concrete findings should be surfaced after one retry"
    )
    assert call_count == 3, f"expected one additional turn, got {call_count}"


def test_inspect_long_final_answer_not_truncated():
    prompt = "Read ATTC.txt and classify safety risk. Do not modify this file. Read-only."
    long_reasoning = (
        "Classification: high-risk/offensive cyber content. "
        "The file includes reconnaissance details, scanning commands, exploitation flow, persistence hints, "
        "and cleanup/deletion behavior. Treat as sensitive and potentially harmful material. "
        "No execution was performed and this analysis is read-only."
    )
    response = json.dumps(
        {
            "plan": "Inspect ATTC.txt and summarize safety findings",
            "reasoning_short": long_reasoning,
            "actions": [],
        }
    )
    logs, _ = _run_case(
        prompt,
        [
            '{"actions":[{"type":"read_file","args":{"path":"ATTC.txt"}}]}',
            response,
        ],
        allow_empty_done_retry=True,
    )
    _assert_success(logs)

    answer_lines = [line for line in logs if line.startswith("FINAL ANSWER:")]
    assert answer_lines, "final answer line expected"
    assert any("No execution was performed and this analysis is read-only." in line for line in answer_lines), (
        "full final answer tail should stay visible (no truncation)"
    )
    assert not any("[collapsed:" in line for line in answer_lines), "final answer should not be collapsed"


def test_inspect_after_read_file_stops_without_empty_done_retry():
    prompt = "Read ATTC.txt and assess it. Do not modify. Read only."
    responses = [
        '{"actions":[{"type":"read_file","args":{"path":"ATTC.txt"}}]}',
        json.dumps(
            {
                "plan": "Inspect ATTC.txt for safety concerns",
                "reasoning_short": "Classification: high-risk/offensive cyber content.",
                "actions": [],
            }
        ),
    ]
    logs, call_count = _run_case(prompt, responses, allow_empty_done_retry=True)
    _assert_success(logs)

    assert any("read_file ATTC.txt" in line for line in logs), "read_file should execute"
    assert not any("EMPTY DONE TOO EARLY" in line for line in logs), "inspect-only should not trigger empty-done retry"
    assert not any(line.startswith("Wrote ") for line in logs), "inspect-only must not write files"
    assert call_count == 2, f"unexpected iterations: {call_count}"


def test_mixed_free_text_and_json_surfaces_useful_answer():
    prompt = "Analyze ATTC.txt only, no edits."
    mixed_response = (
        "Classification: high-risk/offensive cyber content.\n"
        '{"plan":"Inspect ATTC.txt and summarize findings","reasoning_short":"","actions":[]}'
    )
    logs, _ = _run_case(
        prompt,
        [
            '{"actions":[{"type":"read_file","args":{"path":"ATTC.txt"}}]}',
            mixed_response,
        ],
        allow_empty_done_retry=True,
    )
    _assert_success(logs)

    assert any("FINAL ANSWER: Classification: high-risk/offensive cyber content." in line for line in logs), (
        "useful free-text answer should be surfaced"
    )
    assert not any("FINAL ANSWER: Inspect ATTC.txt and summarize findings" in line for line in logs), (
        "plan fallback should not override mixed free-text result"
    )


def test_read_only_safety_analysis_flow():
    prompt = (
        "Read ATTC.txt and assess whether it contains risky, abusive, or offensive cyber instructions. "
        "Do not modify the file. Read-only analysis only."
    )
    responses = [
        '{"actions":[{"type":"read_file","args":{"path":"ATTC.txt"}}]}',
        json.dumps(
            {
                "plan": "Inspect ATTC.txt and report safety classification",
                "reasoning_short": (
                    "Classification: high-risk/offensive cyber content. "
                    "The file describes reconnaissance, command execution, persistence, and cleanup behavior."
                ),
                "actions": [],
            }
        ),
    ]
    logs, _ = _run_case(prompt, responses, allow_empty_done_retry=True)
    _assert_success(logs)

    assert any("TASK INTENT: inspect_only" in line for line in logs), "inspect-only intent expected"
    assert any("PATCH HEURISTICS: disabled" in line for line in logs), "patch heuristics must remain disabled"
    assert any("read_file ATTC.txt" in line for line in logs), "read action should run"
    assert any("FINAL ANSWER BEGIN" in line for line in logs), "final answer should be visible"
    assert any("No files were modified." in line for line in logs), "no-modification status should be explicit"
    assert any("No commands were executed." in line for line in logs), "no-command status should be explicit"
    assert not any(line.startswith("Wrote ") for line in logs), "inspect-only task must not write files"


def test_inspect_low_quality_failure_guard_stops_without_loop():
    prompt = "Read ATTC.txt and classify risk. Do not modify. Read only."
    low_quality = json.dumps(
        {
            "plan": "Inspect ATTC.txt for sensitive content without making changes",
            "reasoning_short": "Need to inspect the file to determine if it contains risky content.",
            "actions": [],
        }
    )
    responses = [
        '{"actions":[{"type":"read_file","args":{"path":"ATTC.txt"}}]}',
        low_quality,
        low_quality,
    ]
    logs, call_count = _run_case(prompt, responses, allow_empty_done_retry=True)

    assert any("LOW QUALITY FINAL ANSWER WARNING:" in line for line in logs), "warning should be surfaced"
    assert any("RUN SUMMARY:" in line and "status=stopped" in line for line in logs), (
        "second low-quality answer should stop without success"
    )
    assert any("stop_reason=LOW QUALITY FINAL ANSWER" in line for line in logs), (
        "stop reason should clearly indicate low-quality final answer"
    )
    assert call_count == 3, f"should stop after second low-quality answer, got {call_count}"


def test_inspect_meaningful_reasoning_short_is_accepted():
    prompt = "Read ATTC.txt and assess risk. Do not modify this file."
    responses = [
        '{"actions":[{"type":"read_file","args":{"path":"ATTC.txt"}}]}',
        json.dumps(
            {
                "plan": "Inspect ATTC.txt for sensitive content",
                "reasoning_short": (
                    "The file contains a penetration testing plan with reconnaissance, exploitation, payload and persistence, "
                    "plus cleanup/deletion steps. Treat as security-sensitive content."
                ),
                "actions": [],
            }
        ),
    ]
    logs, call_count = _run_case(prompt, responses, allow_empty_done_retry=True)
    _assert_success(logs)
    assert not any("INSPECT QUALITY GATE:" in line for line in logs), "meaningful findings should pass quality gate"
    assert any("FINAL ANSWER: The file contains a penetration testing plan" in line for line in logs), (
        "meaningful findings should be accepted immediately"
    )
    assert call_count == 2, f"should stop after first meaningful findings answer, got {call_count}"


def main():
    test_inspect_json_prefers_reasoning_over_plan()
    test_inspect_low_quality_answer_forces_one_more_turn()
    test_inspect_no_read_evidence_forces_read_before_answer()
    test_inspect_meta_analysis_answer_forces_one_more_turn()
    test_inspect_long_final_answer_not_truncated()
    test_inspect_after_read_file_stops_without_empty_done_retry()
    test_mixed_free_text_and_json_surfaces_useful_answer()
    test_read_only_safety_analysis_flow()
    test_inspect_low_quality_failure_guard_stops_without_loop()
    test_inspect_meaningful_reasoning_short_is_accepted()
    print("OK: inspect-only answer extraction/surface regression checks passed")


if __name__ == "__main__":
    main()

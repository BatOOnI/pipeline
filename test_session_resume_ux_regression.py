from session_resume import build_resume_summary, derive_base_and_latest_prompt, format_resume_status


def test_prompt_history_priority():
    snapshot = {
        "prompt": "legacy prompt",
        "prompt_history": [
            {"prompt": "create calc_app.py"},
            {"prompt": "add input validation"},
        ],
        "session_id": "abcdef123456",
        "project_root": "I:/Projekt copilot/TEST",
        "iteration_count": 7,
        "mode": "create",
        "task_shape": "project_generation_task",
    }
    base, latest = derive_base_and_latest_prompt(snapshot)
    assert base == "create calc_app.py"
    assert latest == "add input validation"

    summary = build_resume_summary(snapshot, session_path=".agent/session.json", journal_present=True)
    assert summary["has_state"] is True
    assert summary["prompt_count"] == 2
    status = format_resume_status(summary)
    assert "session=abcdef12" in status
    assert "iter=7" in status


def test_fallback_to_legacy_prompt_fields():
    snapshot = {
        "goal": "inspect ATTC.txt",
        "iteration_count": 1,
    }
    base, latest = derive_base_and_latest_prompt(snapshot)
    assert base == "inspect ATTC.txt"
    assert latest == "inspect ATTC.txt"


def test_empty_snapshot_status():
    summary = build_resume_summary({}, session_path=".agent/session.json", journal_present=False)
    assert summary["has_state"] is False
    assert format_resume_status(summary) == "Resume: no prior session state"


if __name__ == "__main__":
    test_prompt_history_priority()
    test_fallback_to_legacy_prompt_fields()
    test_empty_snapshot_status()
    print("OK: session resume UX helper regression checks passed")

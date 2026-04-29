import json
import os
import shutil
import uuid

import agent_loop
import config
from session_state_store import journal_path_for, load_latest_session_snapshot


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def main():
    root = os.path.join(os.getcwd(), "TEST", f"session_resume_compat_{uuid.uuid4().hex[:8]}")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "calc_app.py"), "w", encoding="utf-8") as handle:
        handle.write("def add(a, b):\n    return a + b\n")

    backup = {}
    original_call_model = agent_loop.call_model
    session_rel = ".agent/session_resume_journal_compat.json"
    session_abs = os.path.abspath(os.path.join(os.getcwd(), session_rel))
    journal_abs = journal_path_for(session_abs)
    logs_first = []
    logs_second = []
    stage = {"run": 1, "calls": 0}

    def fake_call_model(*args, **kwargs):
        stage["calls"] += 1
        if stage["run"] == 1:
            return '{"actions":[{"type":"write_file","args":{"path":"first_run.txt","content":"first\\n"}}]}'
        if stage["run"] == 2 and stage["calls"] == 1:
            return '{"actions":[{"type":"read_file","args":{"path":"calc_app.py"}}]}'
        return "done"

    try:
        _set_config("PROJECT_ROOT", root, backup)
        _set_config("SESSION_FILE", session_rel, backup)
        _set_config("PATCH_FILES", "", backup)
        _set_config("MODE_CONTROL", "AUTO", backup)
        _set_config("RESCUE_MODE", "OFF", backup)
        _set_config("AUTO_VERIFY_PYTHON", False, backup)
        _set_config("AUTO_GIT_CHECKPOINTS", False, backup)
        _set_config("ALLOW_EMPTY_DONE_RETRY", False, backup)
        _set_config("MAX_ITERATIONS", 6, backup)

        agent_loop.call_model = fake_call_model
        agent_loop.run("create first_run.txt", logger=lambda msg: logs_first.append(str(msg)))

        with open(session_abs, "r", encoding="utf-8") as handle:
            payload_first = json.load(handle)
        session_id_first = str(payload_first.get("session_id") or "")
        journal_snapshot_first = load_latest_session_snapshot(session_abs)
        journal_id_first = str(journal_snapshot_first.get("session_id") or "")

        stage["run"] = 2
        stage["calls"] = 0
        _set_config("MAX_ITERATIONS", 1, backup)
        agent_loop.run(
            "Inspect workspace files and report findings only. Do not modify files.",
            logger=lambda msg: logs_second.append(str(msg)),
        )

        with open(session_abs, "r", encoding="utf-8") as handle:
            payload_second = json.load(handle)
        session_id_second = str(payload_second.get("session_id") or "")
        journal_snapshot_second = load_latest_session_snapshot(session_abs)
        journal_id_second = str(journal_snapshot_second.get("session_id") or "")

        summary_second = next((line for line in logs_second if line.startswith("RUN SUMMARY:")), "")

        assert session_id_first, "missing first-run session id"
        assert session_id_first == session_id_second, "session id should persist across resume runs"
        assert session_id_first == journal_id_first == journal_id_second, "session id should match journal snapshot id"
        assert any("TASK INTENT: inspect_only" in line for line in logs_second), "second run should route inspect-only intent"
        assert "outputs=" not in summary_second, f"stale touched-file state leaked into run summary: {summary_second}"

        agent_loop.clear_runtime_session(project_root=root)
        assert not os.path.exists(session_abs), "session file should be removed by clear_runtime_session"
        assert not os.path.exists(journal_abs), "journal file should be removed by clear_runtime_session"
    finally:
        agent_loop.call_model = original_call_model
        for key, value in backup.items():
            setattr(config, key, value)
        shutil.rmtree(root, ignore_errors=True)
        for extra in [session_abs, journal_abs, f"{journal_abs}.1", f"{journal_abs}.2", f"{journal_abs}.3"]:
            try:
                if os.path.exists(extra):
                    os.remove(extra)
            except Exception:
                pass

    print("OK: session resume/journal compatibility regression checks passed")


if __name__ == "__main__":
    main()

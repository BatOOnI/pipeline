import json
import os
import shutil
import uuid

import agent_loop
import config
from session_state_store import journal_path_for


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def main():
    root = os.path.join(os.getcwd(), "TEST", f"runtime_trace_{uuid.uuid4().hex[:8]}")
    os.makedirs(root, exist_ok=True)
    backup = {}
    logs = []
    original_call_model = agent_loop.call_model
    session_rel = f".agent/session_runtime_trace_{uuid.uuid4().hex}.json"
    session_abs = os.path.abspath(os.path.join(os.getcwd(), session_rel))
    journal_abs = journal_path_for(session_abs)
    call_count = {"value": 0}

    responses = [
        '{"actions":[{"type":"write_file","args":{"path":"trace_test.txt","content":"trace smoke\\n"}}]}',
        "done",
    ]

    def fake_call_model(*args, **kwargs):
        idx = call_count["value"]
        call_count["value"] += 1
        if idx < len(responses):
            return responses[idx]
        return "done"

    try:
        _set_config("PROJECT_ROOT", root, backup)
        _set_config("SESSION_FILE", session_rel, backup)
        _set_config("PATCH_FILES", "", backup)
        _set_config("MODE_CONTROL", "AUTO", backup)
        _set_config("RESCUE_MODE", "OFF", backup)
        _set_config("MAX_ITERATIONS", 6, backup)
        _set_config("AUTO_VERIFY_PYTHON", True, backup)
        _set_config("AUTO_GIT_CHECKPOINTS", False, backup)
        _set_config("ALLOW_EMPTY_DONE_RETRY", False, backup)

        agent_loop.call_model = fake_call_model
        agent_loop.run("create trace_test.txt", logger=lambda msg: logs.append(str(msg)))

        assert os.path.exists(journal_abs), "journal file missing"
        with open(journal_abs, "r", encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]

        trace_events = [row for row in rows if str(row.get("type") or "") == "runtime_trace"]
        assert trace_events, "runtime_trace events missing"
        stages = {str(row.get("stage") or "") for row in trace_events}
        required = {"run_start", "iter_start", "model_response", "parse_ok", "action_result", "run_end"}
        missing = sorted(required.difference(stages))
        assert not missing, f"missing runtime trace stages: {missing}"
        assert all(str(row.get("schema") or "") == "runtime_trace/v1" for row in trace_events), "trace schema mismatch"
        assert any(str(row.get("session_id") or "").strip() for row in trace_events), "trace session_id missing"
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

    print("OK: runtime trace journal regression checks passed")


if __name__ == "__main__":
    main()

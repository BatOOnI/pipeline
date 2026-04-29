import os
import shutil
import uuid

import config
from agent_loop import _build_state


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def main():
    root = os.path.join(os.getcwd(), "TEST", f"run_command_only_route_{uuid.uuid4().hex[:8]}")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "calc_app.py"), "w", encoding="utf-8") as handle:
        handle.write("print(1)\n")

    backup = {}
    try:
        _set_config("PROJECT_ROOT", root, backup)
        _set_config("PATCH_FILES", "", backup)
        _set_config("MODE_CONTROL", "AUTO", backup)
        _set_config("RESCUE_MODE", "OFF", backup)

        state = _build_state("Run python -m py_compile calc_app.py and stop. Report output only.")
        assert state.task_intent == "run_command_only", f"unexpected task intent: {state.task_intent}"
        assert state.task_shape == "analysis_report_task", f"unexpected task shape: {state.task_shape}"
    finally:
        for key, value in backup.items():
            setattr(config, key, value)
        shutil.rmtree(root, ignore_errors=True)

    print("OK: run-command-only routing regression checks passed")


if __name__ == "__main__":
    main()

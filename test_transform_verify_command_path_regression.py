import os
import shutil
import uuid

import config
from agent_loop import _build_state, _verify_transform_outputs


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def main():
    root = os.path.join(os.getcwd(), "TEST", f"transform_verify_cmdpath_{uuid.uuid4().hex[:8]}")
    os.makedirs(root, exist_ok=True)
    backup = {}
    try:
        source_path = os.path.join(root, "source.txt")
        with open(source_path, "w", encoding="utf-8") as handle:
            handle.write("simple source body\n")

        _set_config("PROJECT_ROOT", root, backup)
        _set_config("PATCH_FILES", "", backup)
        _set_config("MODE_CONTROL", "AUTO", backup)
        _set_config("RESCUE_MODE", "OFF", backup)
        _set_config("MAX_ITERATIONS", 6, backup)
        _set_config("AUTO_VERIFY_PYTHON", False, backup)

        prompt = (
            "transform and copy source.txt to create calc_app.py, then verify with "
            r"C:\Users\B\AppData\Local\Programs\Python\Python314\python.exe -m py_compile calc_app.py"
        )
        state = _build_state(prompt)

        assert state.task_shape == "transform_copy_task", f"unexpected task shape: {state.task_shape}"
        assert state.derived_allowed_files == ["calc_app.py"], (
            "derived outputs should include only calc_app.py; "
            f"got: {state.derived_allowed_files}"
        )

        output_path = os.path.join(root, "calc_app.py")
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write("print('PLACEHOLDER_OUTPUT')\n")

        verify = _verify_transform_outputs(state)
        assert verify.ok, f"transform verify should pass, got: {verify.summary} | {verify.details}"
    finally:
        for key, value in backup.items():
            setattr(config, key, value)
        shutil.rmtree(root, ignore_errors=True)

    print("OK: transform verify command-path regression checks passed")


if __name__ == "__main__":
    main()

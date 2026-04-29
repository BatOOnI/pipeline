import os
import shutil
import uuid

import config
from agent_loop import _build_state, _required_transform_outputs, _verify_transform_outputs


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def main():
    root = os.path.join(os.getcwd(), "TEST", f"transform_output_noise_{uuid.uuid4().hex[:8]}")
    os.makedirs(root, exist_ok=True)
    backup = {}
    try:
        source_path = os.path.join(root, "ATTC.txt")
        with open(source_path, "w", encoding="utf-8") as handle:
            handle.write("Source text to transform.\n")

        _set_config("PROJECT_ROOT", root, backup)
        _set_config("PATCH_FILES", "", backup)
        _set_config("MODE_CONTROL", "AUTO", backup)
        _set_config("RESCUE_MODE", "OFF", backup)
        _set_config("MAX_ITERATIONS", 6, backup)
        _set_config("AUTO_VERIFY_PYTHON", False, backup)

        prompt = (
            "transform copy ATTC.txt to TEST.txt and keep references 5.3 and defender.You as plain text "
            "inside the content, then stop."
        )
        state = _build_state(prompt)

        assert state.task_shape == "transform_copy_task", f"unexpected task shape: {state.task_shape}"
        required = _required_transform_outputs(state)
        assert required == ["TEST.txt"], f"expected only TEST.txt as transform output, got: {required}"

        output_path = os.path.join(root, "TEST.txt")
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write("Transformed content with 5.3 and defender.You kept as plain text.\n")

        verify = _verify_transform_outputs(state)
        assert verify.ok, f"transform verify should pass, got: {verify.summary} | {verify.details}"
        assert "missing outputs" not in str(verify.details or "").lower(), f"unexpected missing outputs: {verify.details}"
    finally:
        for key, value in backup.items():
            setattr(config, key, value)
        shutil.rmtree(root, ignore_errors=True)

    print("OK: transform output noise filter regression checks passed")


if __name__ == "__main__":
    main()

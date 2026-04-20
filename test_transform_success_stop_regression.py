import os
import shutil
import uuid

import agent_loop
import config


def _set_config(name, value, stash):
    stash[name] = getattr(config, name)
    setattr(config, name, value)


def main():
    tmp = os.path.join(os.getcwd(), "TEST", f"transform_success_stop_{uuid.uuid4().hex[:8]}")
    os.makedirs(tmp, exist_ok=True)
    source = os.path.join(tmp, "szablon.txt")
    with open(source, "w", encoding="utf-8") as handle:
        handle.write(
            "\n".join(
                [
                    "[fusion_builder_container]",
                    "[fusion_text]User content block[/fusion_text]",
                    "[fusion_imageframe image=\"https://example.com/a.jpg\"][/fusion_imageframe]",
                    "[/fusion_builder_container]",
                ]
            )
        )

    logs = []
    original_call_model = agent_loop.call_model
    config_backup = {}
    call_count = {"value": 0}

    def fake_call_model(*args, **kwargs):
        call_count["value"] += 1
        return '{"actions":[{"type":"read_file","args":{"path":"szablon.txt"}}]}'

    try:
        _set_config("PROJECT_ROOT", tmp, config_backup)
        _set_config("PATCH_FILES", "", config_backup)
        _set_config("MODE_CONTROL", "AUTO", config_backup)
        _set_config("RESCUE_MODE", "OFF", config_backup)
        _set_config("MAX_ITERATIONS", 8, config_backup)
        _set_config("AUTO_VERIFY_PYTHON", True, config_backup)

        agent_loop.call_model = fake_call_model

        prompt = "scan szablon.txt and create szablon2.txt with placeholders and DATA.txt"
        agent_loop.run(prompt, logger=lambda msg: logs.append(str(msg)))

        assert os.path.exists(os.path.join(tmp, "szablon2.txt")), "szablon2.txt missing"
        assert os.path.exists(os.path.join(tmp, "DATA.txt")), "DATA.txt missing"
        assert any("TRANSFORM VERIFY: pass" in line for line in logs), "missing transform verify pass log"
        assert any("SUCCESSFUL RUN -> STOP" in line for line in logs), "missing terminal success log"
        assert not any("MAX ITERATIONS EXTENDED" in line for line in logs), "max iterations was unexpectedly extended"
        assert call_count["value"] == 2, f"unexpected extra iterations after transform success: {call_count['value']}"
    finally:
        agent_loop.call_model = original_call_model
        for key, value in config_backup.items():
            setattr(config, key, value)
        shutil.rmtree(tmp, ignore_errors=True)

    print("OK: transform success stop regression checks passed")


if __name__ == "__main__":
    main()

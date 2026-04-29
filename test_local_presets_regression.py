import os
import shutil
import uuid

from local_presets import delete_local_preset, load_local_presets, save_local_presets, upsert_local_preset


def main():
    root = os.path.join(os.getcwd(), "TEST", f"local_presets_{uuid.uuid4().hex[:8]}")
    os.makedirs(root, exist_ok=True)
    presets_path = os.path.join(root, ".agent", "prompt_presets.json")

    try:
        data = load_local_presets(presets_path)
        assert data == {}, "empty preset file should load as empty dict"

        stored = upsert_local_preset(presets_path, "inspect-only", "check file, no edits")
        assert "inspect-only" in stored, "preset should be added"

        stored = upsert_local_preset(presets_path, "inspect-only", "updated text")
        assert stored.get("inspect-only") == "updated text", "preset should be overwritten by name"

        stored = save_local_presets(
            presets_path,
            {
                "zeta": "Z",
                "alpha": "A",
                "": "ignored",
                "beta": "B",
            },
        )
        assert list(stored.keys()) == ["alpha", "beta", "zeta"], "presets should be normalized and sorted"

        loaded = load_local_presets(presets_path)
        assert loaded == stored, "saved presets should roundtrip"

        after_delete = delete_local_preset(presets_path, "beta")
        assert "beta" not in after_delete, "preset should be removed"

        after_missing = delete_local_preset(presets_path, "missing")
        assert after_missing == after_delete, "deleting missing preset should be no-op"
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("OK: local presets regression checks passed")


if __name__ == "__main__":
    main()

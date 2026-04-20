import hashlib
import os
import shutil
import uuid

from agent_loop import PipelineState, _deterministic_transform_copy, _verify_transform_outputs


SAMPLE = """
[fusion_builder_container]
[fusion_text]Hello world from user content[/fusion_text]
[fusion_imageframe image="https://example.com/img/a.jpg"][/fusion_imageframe]
[fusion_images images="https://example.com/img/b.jpg,https://example.com/img/c.jpg"][/fusion_images]
[fusion_content_box]
[fusion_text]Another paragraph with user text[/fusion_text]
[/fusion_content_box]
[/fusion_builder_container]
""".strip()


def _hash(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def main():
    tmp = os.path.join(os.getcwd(), "TEST", f"transform_shortcode_reg_{uuid.uuid4().hex[:8]}")
    os.makedirs(tmp, exist_ok=True)
    try:
        source = os.path.join(tmp, "szablon.txt")
        with open(source, "w", encoding="utf-8") as handle:
            handle.write(SAMPLE)
        source_hash = _hash(source)

        state = PipelineState(
            goal="transform",
            mode="create",
            active_project_root=tmp,
            current_provider="lmstudio",
            task_shape="transform_copy_task",
            transform_primary_source="szablon.txt",
            derived_allowed_files=["szablon2.txt", "DATA.txt"],
            transform_source_hash=source_hash,
        )

        ok, written, err = _deterministic_transform_copy(state)
        assert ok, f"deterministic transform failed: {err}"
        assert "szablon2.txt" in written and "DATA.txt" in written, "required outputs not written"

        out_template = os.path.join(tmp, "szablon2.txt")
        out_data = os.path.join(tmp, "DATA.txt")
        assert os.path.exists(out_template), "szablon2.txt missing"
        assert os.path.exists(out_data), "DATA.txt missing"

        with open(out_template, "r", encoding="utf-8") as handle:
            template_text = handle.read()
        with open(out_data, "r", encoding="utf-8") as handle:
            data_text = handle.read()

        assert "PLACEHOLDER_TEXT_" in template_text, "template placeholders missing"
        assert "PLACEHOLDER_PICTURE_" in template_text, "picture placeholders missing"
        assert "PLACEHOLDER_TEXT_" in data_text or "PLACEHOLDER_PICTURE_" in data_text, "DATA.txt not populated"
        assert _hash(source) == source_hash, "source file changed"

        verify = _verify_transform_outputs(state)
        assert verify.ok, verify.details or verify.summary
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("OK: transform shortcode regression checks passed")


if __name__ == "__main__":
    main()

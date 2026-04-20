import os
import shutil
import uuid

import agent_loop
from executor import find_in_file, read_file


def _make_source(path):
    lines = []
    for idx in range(1, 900):
        if idx % 45 == 0:
            lines.append(f"[fusion_text]Unique headline {idx}[/fusion_text]")
        elif idx % 70 == 0:
            lines.append(f'[fusion_imageframe image_id="{idx}"][/fusion_imageframe]')
        else:
            lines.append(f"Body line {idx} UNIQUE_TOKEN_{idx}")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def run():
    tmp = os.path.abspath(os.path.join("TEST", f"transport_reg_{uuid.uuid4().hex[:8]}"))
    os.makedirs(tmp, exist_ok=True)
    try:
        src = os.path.join(tmp, "szablon.txt")
        _make_source(src)
        rel = "szablon.txt"

        _, cache = agent_loop._structured_large_read_observation(rel, tmp, large_mode="chunk")
        sections = cache.get("sections") or []
        assert sections, "transport sections missing"
        previews = " ".join(str(item.get("preview", "")) for item in sections[:6]).lower()
        assert "fusion_" in previews or "unique_token" in previews, "section previews lost real source markers"

        section_obs = read_file(rel, project_root=tmp, section_id="S1")
        assert section_obs.ok, f"section read failed: {section_obs.details}"
        details = section_obs.details or ""
        assert "Unique headline" in details or "UNIQUE_TOKEN_" in details, "section read did not return raw source text"
        assert "0001: SECTION" not in details, "section read returned placeholder SECTION lines"

        find_obs = find_in_file(rel, "UNIQUE_TOKEN_155", project_root=tmp)
        assert find_obs.ok, f"find_in_file failed: {find_obs.details}"
        assert "matched" in (find_obs.summary or "").lower(), "find_in_file did not match unique source token"

        print("OK: large-file transport regression checks passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    run()

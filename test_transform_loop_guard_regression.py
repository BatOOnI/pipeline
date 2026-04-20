from agent_loop import PipelineState, _attempt_local_fallback, _is_transform_plain_source_read


def _state():
    return PipelineState(
        goal="transform",
        mode="create",
        active_project_root="I:\\Projekt copilot\\TEST",
        current_provider="lmstudio",
        task_shape="transform_copy_task",
        transform_primary_source="szablon.txt",
        derived_allowed_files=["szablon2.txt", "DATA.txt"],
        create_strategy="chunked_rewrite",
        create_phase="chunk_append",
    )


def main():
    state = _state()
    plain_read = {"type": "read_file", "args": {"path": "szablon.txt"}}
    assert _is_transform_plain_source_read(plain_read, state), "plain source read not detected"

    # First plain source read is allowed.
    blocked = _is_transform_plain_source_read(plain_read, state) and state.transform_source_read_seen
    executed_count = 0
    if blocked:
        pass
    else:
        executed_count += 1
        state.transform_source_read_seen = True
    assert executed_count == 1, "first plain source read should be allowed"

    # Second plain source read is blocked and does not count as executed work.
    blocked = _is_transform_plain_source_read(plain_read, state) and state.transform_source_read_seen
    if blocked:
        pass
    else:
        executed_count += 1
    assert blocked, "second plain source read should be blocked"
    assert executed_count == 1, "blocked plain source read must not count as executed"

    # After repeated no-material progress, deterministic fallback step is selected.
    state.transform_no_material_progress_streak = 2
    state.local_fallback_step = 3
    history = []
    logs = []
    switched = _attempt_local_fallback(state, history, logs.append, reason="repeated_plain_read_transform_loop")
    assert switched, "deterministic transform fallback should trigger"
    assert state.create_strategy == "write_file", "deterministic fallback should downgrade create strategy"
    assert state.create_phase == "initial_create", "deterministic fallback should reset to initial create phase"

    print("OK: transform loop guard regression checks passed")


if __name__ == "__main__":
    main()

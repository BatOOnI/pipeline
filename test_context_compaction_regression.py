import agent_loop


def main():
    history = []
    for _ in range(8):
        history.extend(
            [
                "TASK PROFILE: standard_create",
                "MODEL ROUTE: standard (lmstudio)",
                "LOCAL MODEL: qwen/qwen3-coder-30b",
                "CREATE STRATEGY: write_file",
                "CREATE PHASE: verify_or_run",
            ]
        )
    history.extend(
        [
            "ITER 4 | route=standard/lmstudio | reason=default create profile",
            "VERIFY OK: calc_app.py",
            "GIT CHECKPOINT OK",
            "RUN SUMMARY: executed=1 blocked=0 stop_reason=done status=success",
        ]
    )

    compacted_l0 = agent_loop._compact_history(history, 0)
    compacted_l2 = agent_loop._compact_history(history, 2)

    assert "VERIFY OK: calc_app.py" in compacted_l0, "critical verify signal should stay in compacted history"
    assert "RUN SUMMARY:" in compacted_l0, "run summary should stay in compacted history"
    assert compacted_l0.count("TASK PROFILE: standard_create") <= 1, "low-value repeated metadata should be collapsed"
    assert "[context compacted:" in compacted_l0, "compaction marker should be present when lines were dropped"
    assert len(compacted_l2) <= len(compacted_l0), "higher compaction level should not expand context"

    print("OK: context compaction regression checks passed")


if __name__ == "__main__":
    main()

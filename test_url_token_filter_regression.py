import agent_loop


def test_url_domain_tokens_are_not_treated_as_file_targets():
    prompt = (
        "pobierz lumaemu do folderu TEST i skonfiguruj go pod spaceengineers.\n"
        "https://github.com/rm-hull/luma.emulator\n"
        "po zakonczeniu daj instrukcje jak uzywac."
    )
    tokens = agent_loop._extract_prompt_target_candidates(prompt)
    lowered = [str(t).lower() for t in tokens]
    assert "github.com" not in lowered, "domain token should be filtered out"
    assert not any("github.com" in token for token in lowered), "url/domain-like tokens should be filtered out"
    assert not any("://" in token for token in lowered), "url-like tokens should not survive candidate extraction"


def main():
    test_url_domain_tokens_are_not_treated_as_file_targets()
    print("OK: url token filtering regression checks passed")


if __name__ == "__main__":
    main()

from parser import parse_response


def main():
    for raw in ("done", "DONE", "done.", "Done!"):
        data = parse_response(raw, expected_file_count=1, single_file_task=True)
        assert isinstance(data, dict), "parse_response should return dict"
        assert data.get("parse_path") == "done_token", f"unexpected parse path for {raw!r}"
        assert data.get("actions") == [], f"done token should map to empty actions for {raw!r}"

    json_data = parse_response('{"actions":[]}', expected_file_count=1, single_file_task=True)
    assert json_data.get("parse_path") == "cleaned_json", "json path should stay unchanged"
    assert json_data.get("actions") == [], "json empty actions should still work"

    print("OK: parser done token regression checks passed")


if __name__ == "__main__":
    main()

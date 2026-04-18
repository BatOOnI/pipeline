import json
import os
import re


WRAPPER_RE = re.compile(r"<\|[^>\n]+?\|>")


def _strip_code_fences(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json|python|py|text)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _strip_wrappers(text: str) -> str:
    text = WRAPPER_RE.sub(" ", text or "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_balanced_json_candidate(text: str) -> str:
    best = ""
    for open_char, close_char in (("{", "}"), ("[", "]")):
        start = text.find(open_char)
        while start != -1:
            depth = 0
            in_string = False
            escape = False
            for index in range(start, len(text)):
                char = text[index]
                if in_string:
                    if escape:
                        escape = False
                    elif char == "\\":
                        escape = True
                    elif char == '"':
                        in_string = False
                    continue

                if char == '"':
                    in_string = True
                elif char == open_char:
                    depth += 1
                elif char == close_char:
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:index + 1]
                        if len(candidate) > len(best):
                            best = candidate
                        break
            start = text.find(open_char, start + 1)
    return best


def _extract_root_json_candidate(text: str) -> str:
    text = (text or "").lstrip()
    if not text or text[0] not in "{[":
        return ""

    open_char = text[0]
    close_char = "}" if open_char == "{" else "]"
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return text[: index + 1]

    return ""


def _repair_truncated_json(text: str) -> str:
    text = (text or "").strip()
    if not text or text[0] not in "{[":
        return ""

    stack = []
    in_string = False
    escape = False
    for char in text:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            stack.append("}")
        elif char == "[":
            stack.append("]")
        elif char in "}]":
            if not stack or stack[-1] != char:
                return ""
            stack.pop()

    if in_string:
        return ""

    repaired = re.sub(r",\s*$", "", text)
    if not stack and repaired == text:
        return ""
    return repaired + "".join(reversed(stack))


def _extract_text_field_fallback(text: str):
    for key in ("content", "text", "output", "message"):
        match = re.search(rf'"{key}"\s*:\s*"(.*)"\s*\}}?$', text, flags=re.DOTALL)
        if not match:
            continue
        value = match.group(1)
        value = value.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t")
        return value
    return ""


def _convert_legacy_array_format(data):
    actions = []
    for item in data:
        if not isinstance(item, dict):
            continue

        action_name = item.get("action")
        if action_name == "create_file":
            actions.append(
                {
                    "type": "write_file",
                    "args": {
                        "path": item.get("path", ""),
                        "content": item.get("content", ""),
                    },
                }
            )
        elif action_name == "run":
            if "command" in item:
                cmd = item.get("command", "")
            elif "path" in item:
                cmd = ["python", item.get("path", "")]
            else:
                cmd = ""
            actions.append({"type": "run_cmd", "args": {"cmd": cmd}})

    return {
        "plan": "converted legacy action array",
        "reasoning_short": "legacy array normalized",
        "actions": actions,
    }


def _normalize_action_object(item):
    if not isinstance(item, dict):
        return None

    if "type" not in item and "action" in item:
        legacy = _convert_legacy_array_format([item])
        if legacy["actions"]:
            return legacy["actions"][0]
        return None

    if "type" not in item and "path" in item and "content" in item:
        return {
            "type": "write_file",
            "args": {"path": item.get("path", ""), "content": item.get("content", "")},
        }

    action_type = item.get("type")
    if not action_type:
        return None
    action_type = str(action_type)

    args = item.get("args")
    if not isinstance(args, dict):
        args = {
            key: value
            for key, value in item.items()
            if key not in {"type", "action", "plan", "reasoning_short"}
        }

    if action_type == "patch_lines" and "new_content" not in args and "content" in args:
        args = dict(args)
        args["new_content"] = args.get("content", "")

    if action_type in {"insert_before", "insert_after"}:
        args = dict(args)
        if "content" not in args and "new_content" in args:
            args["content"] = args.get("new_content", "")
        if "content_b64" not in args and "new_content_b64" in args:
            args["content_b64"] = args.get("new_content_b64", "")
        if "line_number" not in args and "line" in args:
            args["line_number"] = args.get("line")
        if "line_number" not in args and isinstance(args.get("target"), int):
            args["line_number"] = args.get("target")
        if "anchor" not in args and "target" in args and not isinstance(args.get("target"), int):
            args["anchor"] = args.get("target", "")

    if action_type == "replace_block":
        args = dict(args)
        if "content" not in args and "new_content" in args:
            args["content"] = args.get("new_content", "")
        if "content_b64" not in args and "new_content_b64" in args:
            args["content_b64"] = args.get("new_content_b64", "")
        if "start_line" not in args and isinstance(args.get("start"), int):
            args["start_line"] = args.get("start")
        if "end_line" not in args and isinstance(args.get("end"), int):
            args["end_line"] = args.get("end")
        if "start_anchor" not in args and "start" in args and not isinstance(args.get("start"), int):
            args["start_anchor"] = args.get("start", "")
        if "end_anchor" not in args and "end" in args and not isinstance(args.get("end"), int):
            args["end_anchor"] = args.get("end", "")

    if action_type == "begin_file_rewrite":
        args = dict(args)
        if "expected_parts" not in args and "parts" in args:
            args["expected_parts"] = args.get("parts")

    if action_type == "append_file_chunk":
        args = dict(args)
        if "part" not in args and "index" in args:
            args["part"] = args.get("index")
        if "content" not in args and "chunk" in args:
            args["content"] = args.get("chunk", "")

    return {"type": action_type, "args": args}


def _normalize_root(data, active_target=None, expected_file_count=None, single_file_task=False):
    if isinstance(data, list):
        return _convert_legacy_array_format(data)

    if not isinstance(data, dict):
        raise Exception("PARSE_ERROR: root must be object or array")

    if "actions" in data:
        actions = data.get("actions")
        if isinstance(actions, dict):
            actions = [actions]
        if not isinstance(actions, list):
            raise Exception("PARSE_ERROR: actions must be a list")
        normalized_actions = []
        for item in actions:
            normalized = _normalize_action_object(item)
            if normalized:
                normalized_actions.append(normalized)
        return {
            "plan": data.get("plan", ""),
            "reasoning_short": data.get("reasoning_short", ""),
            "actions": normalized_actions,
        }

    single_action = _normalize_action_object(data)
    if single_action:
        return {
            "plan": data.get("plan", "single action normalized"),
            "reasoning_short": data.get("reasoning_short", "single action normalized"),
            "actions": [single_action],
        }

    for key in ("content", "text", "output", "message"):
        if key in data:
            return parse_response(
                data[key],
                active_target=active_target,
                expected_file_count=expected_file_count,
                single_file_task=single_file_task,
            )

    for key in ("choices", "data", "response"):
        if key in data:
            return parse_response(
                data[key],
                active_target=active_target,
                expected_file_count=expected_file_count,
                single_file_task=single_file_task,
            )

    raise Exception("PARSE_ERROR: missing actions")


def _looks_like_full_source(text: str, active_target: str) -> bool:
    if not active_target:
        return False

    cleaned = _strip_wrappers(_strip_code_fences(text))
    if not cleaned or cleaned.startswith("{") or cleaned.startswith("["):
        return False
    if "<|" in cleaned:
        return False

    non_empty_lines = [line for line in cleaned.splitlines() if line.strip()]
    if len(non_empty_lines) < 3:
        return False

    extension = os.path.splitext(active_target)[1].lower()
    if extension == ".py":
        if not any(
            marker in cleaned
            for marker in (
                "def ",
                "class ",
                "import ",
                "from ",
                "if __name__",
                "print(",
                "try:",
                "for ",
                "while ",
                "tk.",
                "pygame",
                "async ",
            )
        ):
            return False
        if len(cleaned) < 80 and len(non_empty_lines) < 5:
            return False
        return True

    if extension in {".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".json"}:
        return len(cleaned) >= 80

    return len(cleaned) >= 120


def _raw_code_to_actions(text: str, active_target: str):
    cleaned = _strip_wrappers(_strip_code_fences(text))
    return {
        "plan": "raw code response converted",
        "reasoning_short": "provider returned source instead of json",
        "actions": [{"type": "write_file", "args": {"path": active_target, "content": cleaned}}],
    }


def parse_response(text, active_target=None, expected_file_count=None, single_file_task=False):
    if text is None:
        raise Exception("PARSE_ERROR: empty response")

    if isinstance(text, (dict, list)):
        return _normalize_root(
            text,
            active_target=active_target,
            expected_file_count=expected_file_count,
            single_file_task=single_file_task,
        )

    cleaned = _strip_wrappers(_strip_code_fences(str(text)))
    if not cleaned:
        raise Exception("PARSE_ERROR: empty response")

    json_errors = []
    root_candidate = _extract_root_json_candidate(cleaned)
    repaired_cleaned = _repair_truncated_json(cleaned)
    repaired_root = _repair_truncated_json(root_candidate)
    balanced_candidate = _extract_balanced_json_candidate(cleaned)
    repaired_balanced = _repair_truncated_json(balanced_candidate)
    for candidate in (cleaned, repaired_cleaned, root_candidate, repaired_root, balanced_candidate, repaired_balanced):
        candidate = (candidate or "").strip()
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
            return _normalize_root(
                data,
                active_target=active_target,
                expected_file_count=expected_file_count,
                single_file_task=single_file_task,
            )
        except Exception as exc:
            json_errors.append(str(exc))

    text_field = _extract_text_field_fallback(cleaned)
    if text_field:
        return parse_response(
            text_field,
            active_target=active_target,
            expected_file_count=expected_file_count,
            single_file_task=single_file_task,
        )

    if _looks_like_full_source(cleaned, active_target):
        return _raw_code_to_actions(cleaned, active_target)

    detail = "; ".join(x for x in json_errors if x)[:400]
    if expected_file_count == 1 or single_file_task:
        raise Exception(f"PARSE_ERROR: invalid response for single-file task: {detail or 'no valid JSON'}")
    raise Exception(f"PARSE_ERROR: {detail or 'no valid JSON object or array found'}")

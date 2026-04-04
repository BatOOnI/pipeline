import json
import re


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_bracketed(text: str) -> str:
    text = _strip_code_fences(text)

    if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
        return text

    obj = re.search(r"\{.*\}", text, re.DOTALL)
    arr = re.search(r"\[.*\]", text, re.DOTALL)

    candidates = []
    if obj:
        candidates.append(obj.group(0))
    if arr:
        candidates.append(arr.group(0))

    if not candidates:
        raise Exception("PARSE_ERROR: no JSON object or array found")

    return max(candidates, key=len)


def _convert_legacy_array_format(data):
    actions = []
    for item in data:
        if not isinstance(item, dict):
            continue

        action_name = item.get("action")
        if action_name == "create_file":
            actions.append({
                "type": "write_file",
                "args": {
                    "path": item.get("path", ""),
                    "content": item.get("content", "")
                }
            })
        elif action_name == "run":
            if "command" in item:
                cmd = item.get("command", "")
            elif "path" in item:
                cmd = ["python", item.get("path", "")]
            else:
                cmd = ""
            actions.append({
                "type": "run_cmd",
                "args": {
                    "cmd": cmd
                }
            })

    return {
        "plan": "converted legacy action array",
        "reasoning_short": "legacy array normalized",
        "actions": actions
    }


def parse_response(text: str):
    raw = _extract_bracketed(text)
    try:
        data = json.loads(raw)
    except Exception as e:
        raise Exception(f"PARSE_ERROR: {e}")

    if isinstance(data, list):
        return _convert_legacy_array_format(data)

    if not isinstance(data, dict):
        raise Exception("PARSE_ERROR: root must be object or array")

    actions = data.get("actions")
    if actions is None:
        raise Exception("PARSE_ERROR: missing actions")
    if not isinstance(actions, list):
        raise Exception("PARSE_ERROR: actions must be a list")

    return data

import fnmatch
import os
import re
import shlex
from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

import config


MODE_ALIASES = {
    "readonly": "read-only",
    "read-only": "read-only",
    "read_only": "read-only",
    "workspace-write": "workspace-write",
    "workspace_write": "workspace-write",
    "danger-full-access": "danger-full-access",
    "danger_full_access": "danger-full-access",
    "danger": "danger-full-access",
    "allow": "allow",
    "prompt": "prompt",
}

MODE_RANK = {
    "read-only": 0,
    "workspace-write": 1,
    "danger-full-access": 2,
}

READ_ONLY_ACTIONS = {"read_file", "find_in_file", "answer", "final_answer"}
WORKSPACE_WRITE_ACTIONS = {
    "write_file",
    "replace_in_file",
    "insert_before",
    "insert_after",
    "replace_block",
    "patch_lines",
    "begin_file_rewrite",
    "append_file_chunk",
    "finalize_file_rewrite",
    "mkdir",
    "http_get",
    "download_file",
}

READ_ONLY_COMMANDS = {
    "cat",
    "head",
    "tail",
    "ls",
    "find",
    "grep",
    "rg",
    "wc",
    "pwd",
    "whoami",
    "echo",
    "printf",
    "type",
    "where",
    "which",
    "git",
}

WORKSPACE_COMMANDS = {
    "python",
    "py",
    "pytest",
    "pip",
    "npm",
    "node",
    "cargo",
    "go",
    "make",
    "git",
}

DANGEROUS_TOKENS = (
    " rm ",
    " rmdir ",
    " del ",
    " remove-item ",
    " rd ",
    "format ",
    "shutdown ",
    "reboot ",
    "mkfs",
    "reg delete",
    "git reset",
    "git clean",
    "git checkout --",
)

READ_ONLY_GIT_SUBCOMMANDS = {
    "status",
    "log",
    "show",
    "diff",
    "branch",
    "rev-parse",
    "remote",
    "tag",
}

NETWORK_COMMAND_MARKERS = (
    "curl",
    "wget",
    "invoke-webrequest",
    "invoke-restmethod",
    "http://",
    "https://",
    "ftp://",
    "pip install",
    "npm install",
    "git clone",
    "git fetch",
    "git pull",
)

DESTRUCTIVE_BASE_COMMANDS = {
    "rm",
    "rmdir",
    "rd",
    "del",
    "erase",
    "remove-item",
    "ri",
    "format",
}

DESTRUCTIVE_GIT_SUBCOMMANDS = {"reset", "clean", "checkout"}


@dataclass(frozen=True)
class PermissionRule:
    raw: str
    action_type: str
    matcher: str

    def matches(self, action_type: str, target_text: str) -> bool:
        action = (action_type or "").strip().lower()
        if self.action_type not in {"*", action}:
            return False

        matcher = (self.matcher or "*").strip().lower()
        if matcher in {"", "*", "any"}:
            return True

        haystack = (target_text or "").strip().lower()
        if any(ch in matcher for ch in "*?[]"):
            return fnmatch.fnmatch(haystack, matcher)
        return matcher in haystack


def parse_permission_mode(raw_mode) -> str:
    normalized = str(raw_mode or "workspace-write").strip().lower()
    return MODE_ALIASES.get(normalized, "workspace-write")


def _split_rule_tokens(raw_rules) -> List[str]:
    if isinstance(raw_rules, (list, tuple, set)):
        tokens = [str(item or "").strip() for item in raw_rules]
    else:
        text = str(raw_rules or "")
        tokens = [part.strip() for part in re.split(r"[,;\n]", text)]
    return [token for token in tokens if token]


def parse_permission_rules(raw_rules) -> List[PermissionRule]:
    parsed: List[PermissionRule] = []
    for token in _split_rule_tokens(raw_rules):
        action_type = "*"
        matcher = "*"

        if ":" in token:
            left, right = token.split(":", 1)
            action_type = left.strip().lower() or "*"
            matcher = right.strip() or "*"
        elif "(" in token and token.endswith(")"):
            left = token.split("(", 1)[0].strip()
            inside = token[token.find("(") + 1 : -1].strip()
            action_type = left.lower() or "*"
            matcher = inside or "*"
        else:
            action_type = token.strip().lower() or "*"
            matcher = "*"

        parsed.append(PermissionRule(raw=token, action_type=action_type, matcher=matcher.lower()))

    return parsed


def _normalize_command_text(cmd_value) -> str:
    if isinstance(cmd_value, list):
        return " ".join(str(part) for part in cmd_value)
    return str(cmd_value or "")


def _tokenize_command(command_text: str) -> List[str]:
    text = str(command_text or "").strip()
    if not text:
        return []
    try:
        return shlex.split(text, posix=False)
    except Exception:
        return text.split()


def _is_path_like_token(token: str) -> bool:
    value = str(token or "").strip().strip("'\"")
    if not value:
        return False
    lowered = value.lower()
    if lowered.startswith(("http://", "https://", "ftp://")):
        return False
    if value.startswith("-") or value.startswith("/"):
        # keep drive-letter paths like C:/x
        if re.match(r"^[A-Za-z]:[\\/]", value):
            return True
        return False
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return True
    if value.startswith(("\\\\", "/")):
        return True
    if any(ch in value for ch in ("\\", "/")):
        return True
    if "." in value:
        return True
    return False


def _resolve_candidate_path(token: str, project_root: str) -> str:
    value = str(token or "").strip().strip("'\"")
    if not value:
        return ""
    if lowered := value.lower():
        if lowered.startswith(("http://", "https://", "ftp://")):
            return ""
    expanded = os.path.expandvars(os.path.expanduser(value))
    if os.path.isabs(expanded):
        return os.path.abspath(os.path.normpath(expanded))
    base = os.path.abspath(project_root or os.getcwd())
    return os.path.abspath(os.path.normpath(os.path.join(base, expanded)))


def _is_within_workspace(path_value: str, project_root: str) -> bool:
    try:
        candidate = os.path.abspath(path_value)
        root = os.path.abspath(project_root or os.getcwd())
        return os.path.commonpath([candidate, root]) == root
    except Exception:
        return False


def _run_cmd_uses_network(command_text: str, tokens: List[str]) -> bool:
    lowered = str(command_text or "").strip().lower()
    if any(marker in lowered for marker in NETWORK_COMMAND_MARKERS):
        return True
    if tokens:
        first = os.path.basename(tokens[0]).lower().replace(".exe", "")
        if first in {"curl", "wget"}:
            return True
        if first in {"pip", "pip3"} and "install" in [str(t).lower() for t in tokens[1:3]]:
            return True
        if first == "npm" and "install" in [str(t).lower() for t in tokens[1:3]]:
            return True
    return False


def _destructive_paths_outside_workspace(command_text: str, tokens: List[str], project_root: str) -> List[str]:
    if not tokens:
        return []
    first = os.path.basename(tokens[0]).lower().replace(".exe", "")
    lowered = f" {str(command_text or '').lower()} "
    is_destructive = first in DESTRUCTIVE_BASE_COMMANDS or any(token in lowered for token in DANGEROUS_TOKENS)
    if first == "git":
        sub = tokens[1].lower() if len(tokens) > 1 else ""
        if sub in DESTRUCTIVE_GIT_SUBCOMMANDS:
            is_destructive = True
    if not is_destructive:
        return []

    candidate_tokens: List[str] = []
    for token in tokens[1:]:
        normalized = str(token or "").strip().strip("'\"")
        if not normalized:
            continue
        if normalized.startswith("-"):
            continue
        if normalized in {"*", "."}:
            continue
        if _is_path_like_token(normalized):
            candidate_tokens.append(normalized)

    for match in re.findall(r"[A-Za-z]:\\[^\"'\s]+", command_text or ""):
        candidate_tokens.append(match)

    outside = []
    for token in candidate_tokens:
        resolved = _resolve_candidate_path(token, project_root)
        if not resolved:
            continue
        if not _is_within_workspace(resolved, project_root):
            outside.append(token)
    return outside


def classify_run_cmd_permission(cmd_value, project_root=None) -> str:
    command_text = _normalize_command_text(cmd_value)
    lowered = f" {command_text.lower()} "

    if any(token in lowered for token in DANGEROUS_TOKENS):
        return "danger-full-access"

    if any(op in command_text for op in ("&&", "||", ";", "|", ">", "<")):
        return "danger-full-access"

    tokens = _tokenize_command(command_text)
    if not tokens:
        return "danger-full-access"

    first = os.path.basename(tokens[0]).lower().replace(".exe", "")

    if first == "git":
        sub = tokens[1].lower() if len(tokens) > 1 else ""
        if sub in READ_ONLY_GIT_SUBCOMMANDS:
            return "read-only"
        return "workspace-write"

    if first in READ_ONLY_COMMANDS:
        return "read-only"

    if first in WORKSPACE_COMMANDS:
        return "workspace-write"

    return "danger-full-access"


def required_mode_for_action(action_type: str, args: Dict[str, object], project_root=None) -> str:
    action = str(action_type or "").strip().lower()

    if action in READ_ONLY_ACTIONS:
        return "read-only"

    if action in WORKSPACE_WRITE_ACTIONS:
        return "workspace-write"

    if action == "run_cmd":
        return classify_run_cmd_permission(args.get("cmd", ""), project_root=project_root)

    if action in {"action_format_violation", "off_target_patch_action", "permission_denied"}:
        return "read-only"

    return "danger-full-access"


def _rule_target_text(action_type: str, args: Dict[str, object]) -> str:
    action = str(action_type or "").strip().lower()
    if action == "run_cmd":
        return _normalize_command_text(args.get("cmd", ""))
    for key in ("path", "query", "anchor", "start_anchor", "end_anchor"):
        if key in args and str(args.get(key) or "").strip():
            return str(args.get(key))
    return ""


def _parse_decision(decision) -> Tuple[bool, str]:
    if isinstance(decision, dict):
        value = str(decision.get("decision", decision.get("allow", ""))).strip().lower()
        reason = str(decision.get("reason", "")).strip()
    else:
        value = str(decision or "").strip().lower()
        reason = ""

    if value in {"allow", "approved", "approve", "yes", "y", "true", "1"}:
        return True, reason

    return False, reason


def permission_context_from_config() -> Dict[str, object]:
    mode = parse_permission_mode(getattr(config, "PERMISSION_MODE", "workspace-write"))
    allow_rules = parse_permission_rules(getattr(config, "PERMISSION_ALLOW_RULES", ""))
    deny_rules = parse_permission_rules(getattr(config, "PERMISSION_DENY_RULES", ""))
    ask_rules = parse_permission_rules(getattr(config, "PERMISSION_ASK_RULES", ""))

    chunks = []
    if allow_rules:
        chunks.append("allow=" + ", ".join(rule.raw for rule in allow_rules[:6]))
    if deny_rules:
        chunks.append("deny=" + ", ".join(rule.raw for rule in deny_rules[:6]))
    if ask_rules:
        chunks.append("ask=" + ", ".join(rule.raw for rule in ask_rules[:6]))

    return {
        "mode": mode,
        "allow_rules": allow_rules,
        "deny_rules": deny_rules,
        "ask_rules": ask_rules,
        "summary": " | ".join(chunks),
    }


def authorize_action(
    action_type: str,
    args: Dict[str, object],
    project_root=None,
    permission_decider: Callable[[Dict[str, object]], object] = None,
) -> Dict[str, object]:
    context = permission_context_from_config()
    mode = str(context["mode"])
    allow_rules: List[PermissionRule] = list(context["allow_rules"])
    deny_rules: List[PermissionRule] = list(context["deny_rules"])
    ask_rules: List[PermissionRule] = list(context["ask_rules"])

    action = str(action_type or "").strip().lower()
    args = dict(args or {})
    target_text = _rule_target_text(action, args)
    required_mode = required_mode_for_action(action, args, project_root=project_root)
    network_enabled = bool(getattr(config, "NETWORK_ENABLED", True))

    if action in {"answer", "final_answer"}:
        return {
            "allowed": True,
            "current_mode": mode,
            "required_mode": "read-only",
            "reason": "Answer action is always allowed.",
            "rule": "answer_always_allowed",
        }

    if action in {"http_get", "download_file"} and not network_enabled:
        return {
            "allowed": False,
            "current_mode": mode,
            "required_mode": required_mode,
            "reason": "Network access is disabled by configuration (NETWORK_ENABLED=OFF).",
            "rule": "network_disabled",
        }

    if action == "run_cmd":
        command_text = _normalize_command_text(args.get("cmd", ""))
        tokens = _tokenize_command(command_text)
        if (not network_enabled) and _run_cmd_uses_network(command_text, tokens):
            return {
                "allowed": False,
                "current_mode": mode,
                "required_mode": required_mode,
                "reason": "Network access is disabled by configuration (NETWORK_ENABLED=OFF).",
                "rule": "network_disabled",
            }
        outside_targets = _destructive_paths_outside_workspace(command_text, tokens, project_root or os.getcwd())
        if outside_targets:
            preview = ", ".join(outside_targets[:4])
            if len(outside_targets) > 4:
                preview += ", ..."
            return {
                "allowed": False,
                "current_mode": mode,
                "required_mode": required_mode,
                "reason": f"Destructive command targets path outside workspace: {preview}",
                "rule": "outside_workspace_destructive",
            }

    deny_rule = next((rule for rule in deny_rules if rule.matches(action, target_text)), None)
    if deny_rule is not None:
        return {
            "allowed": False,
            "current_mode": mode,
            "required_mode": required_mode,
            "reason": f"Denied by rule '{deny_rule.raw}'.",
            "rule": deny_rule.raw,
        }

    ask_rule = next((rule for rule in ask_rules if rule.matches(action, target_text)), None)
    allow_rule = next((rule for rule in allow_rules if rule.matches(action, target_text)), None)

    if mode == "allow":
        return {
            "allowed": True,
            "current_mode": mode,
            "required_mode": required_mode,
            "reason": "Allowed by mode allow.",
            "rule": allow_rule.raw if allow_rule else "",
        }

    needs_prompt = False
    prompt_reason = ""

    if ask_rule is not None:
        needs_prompt = True
        prompt_reason = f"Matched ask rule '{ask_rule.raw}'."
    elif mode == "prompt":
        needs_prompt = True
        prompt_reason = "Mode prompt requires confirmation for each action."
    elif allow_rule is not None:
        return {
            "allowed": True,
            "current_mode": mode,
            "required_mode": required_mode,
            "reason": f"Allowed by rule '{allow_rule.raw}'.",
            "rule": allow_rule.raw,
        }
    else:
        current_rank = MODE_RANK.get(mode, MODE_RANK["workspace-write"])
        required_rank = MODE_RANK.get(required_mode, MODE_RANK["danger-full-access"])
        if current_rank < required_rank:
            needs_prompt = True
            prompt_reason = (
                f"Action '{action}' requires '{required_mode}' but current mode is '{mode}'."
            )

    if needs_prompt:
        if callable(permission_decider):
            request = {
                "action_type": action,
                "args": args,
                "current_mode": mode,
                "required_mode": required_mode,
                "reason": prompt_reason,
                "target": target_text,
            }
            decision = permission_decider(request)
            approved, decision_reason = _parse_decision(decision)
            if approved:
                return {
                    "allowed": True,
                    "current_mode": mode,
                    "required_mode": required_mode,
                    "reason": decision_reason or "Approved by permission decider.",
                    "rule": ask_rule.raw if ask_rule else "",
                }
            return {
                "allowed": False,
                "current_mode": mode,
                "required_mode": required_mode,
                "reason": decision_reason or (prompt_reason + " Approval denied."),
                "rule": ask_rule.raw if ask_rule else "",
            }

        return {
            "allowed": False,
            "current_mode": mode,
            "required_mode": required_mode,
            "reason": prompt_reason + " No permission decider configured.",
            "rule": ask_rule.raw if ask_rule else "",
        }

    return {
        "allowed": True,
        "current_mode": mode,
        "required_mode": required_mode,
        "reason": "Allowed by active mode.",
        "rule": "",
    }

import os
import sys
import config
from providers import call_model
from parser import parse_response
from executor import write_file, run_cmd


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)
            s.flush()

    def flush(self):
        for s in self.streams:
            s.flush()


def normalize_rel_path(rel_path: str) -> str:
    rel_path = (rel_path or "").replace("/", os.sep).replace("\\", os.sep).strip()
    rel_path = rel_path.lstrip(".\\/")
    root_name = os.path.basename(os.path.normpath(config.PROJECT_ROOT))
    prefixes = [root_name + os.sep, "TEST" + os.sep]
    changed = True
    while changed:
        changed = False
        for prefix in prefixes:
            if rel_path.startswith(prefix):
                rel_path = rel_path[len(prefix):]
                changed = True
    return rel_path or "app.py"


def normalize_cmd_paths(cmd):
    def fix_one(p):
        if not isinstance(p, str):
            return p
        p = p.replace("/", os.sep).replace("\\", os.sep).strip()
        if p.startswith("TEST" + os.sep) or p.startswith(os.path.basename(os.path.normpath(config.PROJECT_ROOT)) + os.sep):
            p = normalize_rel_path(p)
        return p

    if isinstance(cmd, list):
        return [fix_one(x) for x in cmd]
    return fix_one(cmd)


def build_prompt(user_prompt, history):
    hist = "\n".join(history[-8:]) if history else "None"
    return f'''
You are a coding agent.

Project root is: {config.PROJECT_ROOT}

TASK:
{user_prompt}

OBSERVATIONS:
{hist}

Return ONLY JSON with this exact shape:
{{
  "plan": "short plan",
  "reasoning_short": "short reason",
  "actions": [
    {{
      "type": "write_file",
      "args": {{
        "path": "app.py",
        "content": "print(\"Hello World\")"
      }}
    }}
  ]
}}

Rules:
- Allowed action types: write_file, run_cmd
- ALL file paths must be relative to project root only
- NEVER prefix paths with "{config.PROJECT_ROOT}/"
- For Python commands on Windows, use "python", not "python3"
- If the user asked to run the program, include run_cmd
- Interactive CLI programs should still be run once; the executor handles non-interactive stdin/EOF safely
- If task is complete, return:
{{
  "plan": "done",
  "reasoning_short": "task completed",
  "actions": []
}}
'''.strip()


def run(prompt, logger=print, stop_checker=None):
    os.makedirs(config.PROJECT_ROOT, exist_ok=True)

    log_path = os.path.join(os.getcwd(), config.LOG_FILE)

    with open(log_path, "w", encoding="utf-8") as log_file:
        tee = Tee(sys.stdout, log_file)

        def log(msg=""):
            tee.write(str(msg) + "\n")
            if logger is not None:
                logger(str(msg))

        history = []
        consecutive_parse_errors = 0

        for i in range(config.MAX_ITERATIONS):
            if stop_checker and stop_checker():
                log("STOP REQUESTED")
                break

            log(f"\nITER {i}")
            raw = call_model(build_prompt(prompt, history))
            log("RAW: " + str(raw))

            try:
                data = parse_response(raw)
                consecutive_parse_errors = 0
            except Exception as e:
                msg = str(e)
                log(msg)
                history.append(msg)
                consecutive_parse_errors += 1
                if consecutive_parse_errors >= 2:
                    log("STOP: repeated parse errors")
                    break
                continue

            actions = data.get("actions", [])
            if not actions:
                log("DONE")
                break

            total_actions = len(actions)
            executed_count = 0
            had_failure = False
            had_run_cmd = False

            for a in actions:
                if stop_checker and stop_checker():
                    log("STOP REQUESTED")
                    return log_path

                t = a.get("type")
                args = a.get("args", {})

                if t == "write_file":
                    rel_path = normalize_rel_path(args.get("path", ""))
                    full_path = os.path.join(config.PROJECT_ROOT, rel_path)
                    obs = write_file(full_path, args.get("content", ""))

                elif t == "run_cmd":
                    if not config.AUTO_RUN_COMMANDS:
                        log("AUTO_RUN_COMMANDS disabled -> skipping run_cmd")
                        continue
                    had_run_cmd = True
                    cmd = normalize_cmd_paths(args.get("cmd", ""))
                    obs = run_cmd(cmd, cwd=config.PROJECT_ROOT)

                else:
                    log(f"UNKNOWN ACTION: {t}")
                    had_failure = True
                    continue

                executed_count += 1
                log(obs.summary)
                if obs.details:
                    log(obs.details)

                history.append(obs.summary + (" | " + obs.details if obs.details else ""))

                if not obs.ok:
                    had_failure = True

            all_actions_executed = (executed_count == total_actions) or (not config.AUTO_RUN_COMMANDS and executed_count > 0)

            if all_actions_executed and not had_failure:
                if had_run_cmd:
                    log("SUCCESSFUL RUN -> STOP")
                else:
                    log("SUCCESSFUL WRITE -> STOP")
                break

        log(f"LOG SAVED TO: {log_path}")
        return log_path

import os
import subprocess
import sys
import tempfile
import time
from typing import Dict, List

import requests

import config
from permission_flow import parse_permission_mode


def _result(check_id: str, label: str, status: str, detail: str) -> Dict[str, str]:
    value = str(status or "WARN").strip().upper()
    if value not in {"OK", "WARN", "FAIL"}:
        value = "WARN"
    return {
        "id": str(check_id or "").strip(),
        "label": str(label or "").strip(),
        "status": value,
        "detail": str(detail or "").strip(),
    }


def _lmstudio_models_url(lmstudio_url: str) -> str:
    url = str(lmstudio_url or "").strip()
    if not url:
        return ""
    for suffix in ("/chat/completions", "/completions"):
        if suffix in url:
            return url.rsplit(suffix, 1)[0] + "/models"
    return url.rstrip("/") + "/models"


def _lmstudio_headers(lmstudio_api_key: str) -> Dict[str, str]:
    token = str(lmstudio_api_key or "").strip()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _openai_base_url() -> str:
    configured = str(getattr(config, "OPENAI_BASE_URL", "") or os.environ.get("OPENAI_BASE_URL", "")).strip()
    if configured:
        return configured.rstrip("/")
    return "https://api.openai.com/v1"


def _deepseek_base_url() -> str:
    return str(getattr(config, "DEEPSEEK_BASE_URL", "") or os.environ.get("DEEPSEEK_BASE_URL", "")).strip()


def _deepseek_key() -> str:
    return str(getattr(config, "DEEPSEEK_API_KEY", "") or os.environ.get("DEEPSEEK_API_KEY", "")).strip()


def _run_command(command: List[str], cwd: str, timeout: int = 8) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=cwd or None,
        text=True,
        capture_output=True,
        timeout=max(2, int(timeout or 8)),
        check=False,
    )


def _is_timeout_like_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    if "timed out" in text or "timeout" in text:
        return True
    if isinstance(exc, requests.exceptions.Timeout):
        return True
    return False


def run_preflight_doctor(
    project_root: str = "",
    selected_provider: str = "",
    cwd: str = "",
    timeout_seconds: int = 8,
) -> Dict[str, object]:
    started_at_ms = int(time.time() * 1000)
    base_cwd = os.path.abspath(cwd or os.getcwd())
    root = str(project_root or "").strip()
    if root:
        root = os.path.abspath(root)
    else:
        root = os.path.abspath(config.ACTIVE_PROJECT_ROOT or os.path.join(base_cwd, str(config.PROJECT_ROOT or "TEST")))
    provider = str(selected_provider or config.PROVIDER or "").strip().lower()
    if not provider:
        provider = "lmstudio"

    results: List[Dict[str, str]] = []

    lm_url = str(config.LMSTUDIO_URL or "").strip()
    lm_model = str(config.LMSTUDIO_MODEL or "").strip()
    lm_key = str(getattr(config, "LMSTUDIO_API_KEY", "") or "").strip()
    models_url = _lmstudio_models_url(lm_url)
    lm_headers = _lmstudio_headers(lm_key)

    lm_url_ok = False
    if not lm_url:
        results.append(_result("lmstudio_url", "LM Studio base URL reachable", "FAIL", "LMSTUDIO_URL is empty."))
    else:
        try:
            response = requests.get(models_url, headers=lm_headers or None, timeout=max(3, int(timeout_seconds)))
            response.raise_for_status()
            lm_url_ok = True
            results.append(_result("lmstudio_url", "LM Studio base URL reachable", "OK", f"GET {models_url} -> HTTP {response.status_code}"))
        except Exception as exc:
            results.append(_result("lmstudio_url", "LM Studio base URL reachable", "FAIL", f"{models_url}: {exc}"))

    lm_model_ok = False
    lm_model_timed_out = False
    if not lm_model:
        results.append(_result("lmstudio_model", "Selected LM Studio model responds", "FAIL", "LMSTUDIO_MODEL is empty."))
    elif not lm_url:
        results.append(_result("lmstudio_model", "Selected LM Studio model responds", "FAIL", "LM Studio URL is empty."))
    else:
        payload = {
            "model": lm_model,
            "messages": [{"role": "user", "content": "doctor preflight ping"}],
            "temperature": 0,
            "max_tokens": 16,
        }
        model_check_timeout = max(15, min(int(getattr(config, "MODEL_TIMEOUT", timeout_seconds) or timeout_seconds), 90))
        try:
            response = requests.post(
                lm_url,
                headers=lm_headers or None,
                json=payload,
                timeout=model_check_timeout,
            )
            response.raise_for_status()
            body = response.json() if response.content else {}
            choices = body.get("choices") if isinstance(body, dict) else None
            if isinstance(choices, list) and choices:
                lm_model_ok = True
                results.append(_result("lmstudio_model", "Selected LM Studio model responds", "OK", f"{lm_model} returned {len(choices)} choice(s)."))
            else:
                results.append(_result("lmstudio_model", "Selected LM Studio model responds", "FAIL", f"{lm_model} returned no choices."))
        except Exception as exc:
            if _is_timeout_like_error(exc):
                lm_model_timed_out = True
                results.append(
                    _result(
                        "lmstudio_model",
                        "Selected LM Studio model responds",
                        "WARN",
                        f"{lm_model}: response exceeded {model_check_timeout}s timeout (model likely cold/slow).",
                    )
                )
            else:
                results.append(_result("lmstudio_model", "Selected LM Studio model responds", "FAIL", f"{lm_model}: {exc}"))

    openai_key = str(config.OPENAI_API_KEY or "").strip()
    openai_model = str(config.OPENAI_MODEL or "").strip()
    openai_base = _openai_base_url()
    models_endpoint = openai_base if openai_base.endswith("/models") else f"{openai_base}/models"
    openai_ok = False
    if not openai_key:
        results.append(_result("openai_connectivity", "OpenAI key/base URL (if configured)", "WARN", "OpenAI key not configured; skipping API connectivity check."))
    else:
        try:
            response = requests.get(
                models_endpoint,
                headers={"Authorization": f"Bearer {openai_key}"},
                timeout=max(4, int(timeout_seconds) + 2),
            )
            response.raise_for_status()
            openai_ok = True
            results.append(
                _result(
                    "openai_connectivity",
                    "OpenAI key/base URL (if configured)",
                    "OK",
                    f"{models_endpoint} reachable; model={openai_model or '(not set)'}",
                )
            )
        except Exception as exc:
            results.append(_result("openai_connectivity", "OpenAI key/base URL (if configured)", "FAIL", f"{models_endpoint}: {exc}"))

    deepseek_base = _deepseek_base_url()
    deepseek_key = _deepseek_key()
    if deepseek_base or deepseek_key:
        results.append(
            _result(
                "deepseek_placeholder",
                "DeepSeek key/base URL status",
                "WARN",
                "DeepSeek credentials/base URL detected, but provider path is not implemented in this runtime yet.",
            )
        )
    else:
        results.append(
            _result(
                "deepseek_placeholder",
                "DeepSeek key/base URL status",
                "OK",
                "DeepSeek not configured. Placeholder support check passed.",
            )
        )

    py_exec = str(sys.executable or "").strip()
    if not py_exec:
        results.append(_result("python_exec", "Python executable path works", "FAIL", "sys.executable is empty."))
    else:
        try:
            proc = _run_command([py_exec, "-V"], cwd=base_cwd, timeout=timeout_seconds)
            if proc.returncode == 0:
                results.append(_result("python_exec", "Python executable path works", "OK", (proc.stdout or proc.stderr or py_exec).strip()))
            else:
                detail = (proc.stderr or proc.stdout or "").strip()
                results.append(_result("python_exec", "Python executable path works", "FAIL", f"Exit {proc.returncode}: {detail}"))
        except Exception as exc:
            results.append(_result("python_exec", "Python executable path works", "FAIL", str(exc)))

    try:
        shell_proc = _run_command(["powershell", "-NoProfile", "-Command", "Write-Output doctor_shell_ok"], cwd=base_cwd, timeout=timeout_seconds)
        text = ((shell_proc.stdout or "") + " " + (shell_proc.stderr or "")).strip()
        if shell_proc.returncode == 0 and "doctor_shell_ok" in text:
            results.append(_result("shell_exec", "Shell execution works", "OK", "PowerShell invocation succeeded."))
        else:
            results.append(_result("shell_exec", "Shell execution works", "FAIL", f"Exit {shell_proc.returncode}: {text[:240]}"))
    except Exception as exc:
        results.append(_result("shell_exec", "Shell execution works", "FAIL", str(exc)))

    try:
        git_proc = _run_command(["git", "-C", root, "rev-parse", "--show-toplevel"], cwd=base_cwd, timeout=timeout_seconds)
        out = (git_proc.stdout or "").strip()
        err = (git_proc.stderr or "").strip()
        if git_proc.returncode == 0 and out:
            results.append(_result("git_root", "Git root detection works", "OK", out))
        elif "not a git repository" in (out + " " + err).lower():
            results.append(_result("git_root", "Git root detection works", "WARN", f"{root} is not a git repository."))
        else:
            results.append(_result("git_root", "Git root detection works", "FAIL", f"Exit {git_proc.returncode}: {(err or out)[:240]}"))
    except Exception as exc:
        results.append(_result("git_root", "Git root detection works", "FAIL", str(exc)))

    try:
        os.makedirs(root, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="doctor_", suffix=".tmp", dir=root, delete=False, mode="w", encoding="utf-8") as handle:
            handle.write("doctor writable test\n")
            temp_path = handle.name
        os.remove(temp_path)
        results.append(_result("workspace_writable", "Workspace is writable", "OK", root))
    except Exception as exc:
        results.append(_result("workspace_writable", "Workspace is writable", "FAIL", f"{root}: {exc}"))

    raw_mode = str(getattr(config, "PERMISSION_MODE", "workspace-write") or "workspace-write").strip()
    parsed_mode = parse_permission_mode(raw_mode)
    if parsed_mode == "workspace-write" and raw_mode.lower().strip() not in {"workspace-write", "workspace_write"}:
        results.append(
            _result(
                "permission_mode",
                "Permission mode is valid",
                "WARN",
                f"Configured mode '{raw_mode}' normalized to '{parsed_mode}'.",
            )
        )
    else:
        results.append(_result("permission_mode", "Permission mode is valid", "OK", f"effective={parsed_mode}"))

    if provider == "lmstudio":
        if lm_url_ok and lm_model_ok:
            results.append(_result("provider_route", "Selected provider route is usable", "OK", "lmstudio route ready."))
        elif lm_url_ok and lm_model_timed_out:
            results.append(
                _result(
                    "provider_route",
                    "Selected provider route is usable",
                    "WARN",
                    "lmstudio route reachable but model check timed out (slow/warmup).",
                )
            )
        else:
            results.append(_result("provider_route", "Selected provider route is usable", "FAIL", "lmstudio selected but URL/model check failed."))
    elif provider == "openai":
        if openai_ok:
            results.append(_result("provider_route", "Selected provider route is usable", "OK", "openai route ready."))
        elif not openai_key:
            results.append(_result("provider_route", "Selected provider route is usable", "FAIL", "openai selected but API key is missing."))
        else:
            results.append(_result("provider_route", "Selected provider route is usable", "FAIL", "openai selected but connectivity check failed."))
    elif provider in {"deepseek"}:
        results.append(_result("provider_route", "Selected provider route is usable", "FAIL", "deepseek provider route not implemented yet."))
    else:
        results.append(_result("provider_route", "Selected provider route is usable", "FAIL", f"Unknown provider '{provider}'."))

    session_path = os.path.abspath(os.path.join(base_cwd, str(config.SESSION_FILE or ".agent/session.json")))
    session_dir = os.path.dirname(session_path)
    try:
        if session_dir:
            os.makedirs(session_dir, exist_ok=True)
        os.makedirs(root, exist_ok=True)
        results.append(
            _result(
                "project_session_dirs",
                "Project/session directories are available",
                "OK",
                f"project={root} | session_dir={session_dir}",
            )
        )
    except Exception as exc:
        results.append(
            _result(
                "project_session_dirs",
                "Project/session directories are available",
                "FAIL",
                str(exc),
            )
        )

    ok_count = sum(1 for row in results if row.get("status") == "OK")
    warn_count = sum(1 for row in results if row.get("status") == "WARN")
    fail_count = sum(1 for row in results if row.get("status") == "FAIL")
    overall = "OK"
    if fail_count > 0:
        overall = "FAIL"
    elif warn_count > 0:
        overall = "WARN"

    return {
        "started_at_ms": started_at_ms,
        "finished_at_ms": int(time.time() * 1000),
        "provider": provider,
        "project_root": root,
        "session_file": session_path,
        "results": results,
        "summary": {
            "ok": ok_count,
            "warn": warn_count,
            "fail": fail_count,
            "overall": overall,
        },
    }

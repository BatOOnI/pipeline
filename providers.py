import json
import threading
import time

import requests

import config


def _flatten_content(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            part = _flatten_content(item)
            if part:
                parts.append(part)
        return "\n".join(parts).strip()
    if isinstance(value, dict):
        if "text" in value and isinstance(value["text"], str):
            return value["text"]
        if "content" in value:
            return _flatten_content(value["content"])
        if "message" in value:
            return _flatten_content(value["message"])
        if "output_text" in value and isinstance(value["output_text"], str):
            return value["output_text"]
        if "output" in value:
            return _flatten_content(value["output"])
        if "choices" in value:
            return _flatten_content(value["choices"])
    return ""


def _safe_extract_content(data):
    content = _flatten_content(data)
    if content:
        return content
    raise Exception(f"Bad model response shape: {json.dumps(data)[:1200]}")


def _raise_for_http_error(response, provider_name):
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        body = ""
        try:
            body = response.text[:2000]
        except Exception:
            body = ""
        raise Exception(f"{provider_name} HTTP {response.status_code}: {body or exc}") from exc


def _lmstudio_models_url(lmstudio_url):
    url = (lmstudio_url or config.LMSTUDIO_URL or "").strip()
    if not url:
        raise Exception("LM Studio URL is empty")
    for suffix in ("/chat/completions", "/completions"):
        if suffix in url:
            return url.rsplit(suffix, 1)[0] + "/models"
    return url.rstrip("/") + "/models"


def _lmstudio_headers(api_key=None):
    token = (api_key or config.LMSTUDIO_API_KEY or "").strip()
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}


def _openai_use_max_completion_tokens(model_name):
    name = (model_name or "").strip().lower()
    return name.startswith("gpt-5")


class ModelRequestHandle:
    def __init__(self):
        self.finished = threading.Event()
        self.cancel_requested = threading.Event()
        self._session = None
        self.result = None
        self.error = None

    def bind_session(self, session):
        self._session = session

    def cancel(self):
        self.cancel_requested.set()
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass


def _request_worker(handle, provider_name, url, *, headers=None, payload=None):
    session = requests.Session()
    handle.bind_session(session)
    try:
        response = session.post(
            url,
            headers=headers,
            json=payload,
            timeout=(10, None),
        )
        _raise_for_http_error(response, provider_name)
        handle.result = _safe_extract_content(response.json())
    except requests.RequestException as exc:
        if handle.cancel_requested.is_set():
            handle.error = Exception("MODEL REQUEST KILLED BY USER")
        else:
            handle.error = Exception(f"{provider_name} REQUEST FAILED: {exc}")
    except Exception as exc:
        handle.error = exc
    finally:
        try:
            session.close()
        except Exception:
            pass
        handle.finished.set()


def _wait_for_model_result(handle, provider_name, timeout_seconds, timeout_handler=None, stop_checker=None):
    timeout_seconds = max(1, int(timeout_seconds or config.MODEL_TIMEOUT or 120))
    timeout_attempt = 1
    deadline = time.monotonic() + timeout_seconds
    while True:
        if callable(stop_checker) and stop_checker():
            handle.cancel()
            raise Exception("MODEL REQUEST KILLED BY USER")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if timeout_handler is None:
                handle.cancel()
                raise Exception(f"{provider_name} REQUEST TIMEOUT after {timeout_seconds}s")
            decision = timeout_handler(handle, timeout_seconds, timeout_attempt)
            if decision == "kill":
                handle.cancel()
                raise Exception("MODEL REQUEST KILLED BY USER")
            timeout_attempt += 1
            deadline = time.monotonic() + timeout_seconds
            continue
        if handle.finished.wait(min(0.2, remaining)):
            break

    if handle.cancel_requested.is_set() and handle.error is None:
        raise Exception("MODEL REQUEST KILLED BY USER")
    if handle.error is not None:
        raise handle.error
    return handle.result


class ProviderAdapter:
    name = ""

    def call_model(self, prompt, max_output_tokens, timeout_seconds, timeout_handler, stop_checker):
        raise NotImplementedError

    def list_models(self, lmstudio_url=None, openai_api_key=None, lmstudio_api_key=None):
        raise NotImplementedError


class LmStudioProviderAdapter(ProviderAdapter):
    name = "lmstudio"

    def call_model(self, prompt, max_output_tokens, timeout_seconds, timeout_handler, stop_checker):
        payload = {
            "model": config.LMSTUDIO_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": max_output_tokens,
        }
        handle = ModelRequestHandle()
        thread = threading.Thread(
            target=_request_worker,
            args=(handle, "LMSTUDIO", config.LMSTUDIO_URL),
            kwargs={"headers": _lmstudio_headers(), "payload": payload},
            daemon=True,
        )
        thread.start()
        return _wait_for_model_result(
            handle,
            "LMSTUDIO",
            timeout_seconds,
            timeout_handler=timeout_handler,
            stop_checker=stop_checker,
        )

    def list_models(self, lmstudio_url=None, openai_api_key=None, lmstudio_api_key=None):
        url = _lmstudio_models_url(lmstudio_url or config.LMSTUDIO_URL)
        try:
            response = requests.get(
                url,
                headers=_lmstudio_headers(lmstudio_api_key),
                timeout=max(5, int(config.MODEL_TIMEOUT)),
            )
        except requests.RequestException as exc:
            raise Exception(f"LMSTUDIO MODEL LIST FAILED: {exc}") from exc
        _raise_for_http_error(response, "LMSTUDIO")
        return _extract_model_ids(response.json(), self.name)


class OpenAIProviderAdapter(ProviderAdapter):
    name = "openai"

    def call_model(self, prompt, max_output_tokens, timeout_seconds, timeout_handler, stop_checker):
        if not config.OPENAI_API_KEY.strip():
            raise Exception("OPENAI_API_KEY is empty in config.py / GUI field")

        headers = {
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }
        model_name = config.OPENAI_MODEL
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
        if _openai_use_max_completion_tokens(model_name):
            payload["max_completion_tokens"] = max_output_tokens
        else:
            payload["max_tokens"] = max_output_tokens

        handle = ModelRequestHandle()
        thread = threading.Thread(
            target=_request_worker,
            args=(handle, "OPENAI", "https://api.openai.com/v1/chat/completions"),
            kwargs={"headers": headers, "payload": payload},
            daemon=True,
        )
        thread.start()
        return _wait_for_model_result(
            handle,
            "OPENAI",
            timeout_seconds,
            timeout_handler=timeout_handler,
            stop_checker=stop_checker,
        )

    def list_models(self, lmstudio_url=None, openai_api_key=None, lmstudio_api_key=None):
        api_key = (openai_api_key or config.OPENAI_API_KEY or "").strip()
        if not api_key:
            raise Exception("OpenAI API key is required to list models.")
        try:
            response = requests.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=max(5, int(config.MODEL_TIMEOUT)),
            )
        except requests.RequestException as exc:
            raise Exception(f"OPENAI MODEL LIST FAILED: {exc}") from exc
        _raise_for_http_error(response, "OPENAI")
        return _extract_model_ids(response.json(), self.name)


_PROVIDER_ADAPTERS = {
    "lmstudio": LmStudioProviderAdapter(),
    "openai": OpenAIProviderAdapter(),
}


def _extract_model_ids(data, provider_name):
    models = []
    for item in data.get("data", []):
        model_id = item.get("id")
        if isinstance(model_id, str) and model_id.strip():
            models.append(model_id.strip())

    unique_models = sorted(dict.fromkeys(models))
    if not unique_models:
        raise Exception(f"No models returned by {provider_name}.")
    return unique_models


def _resolve_provider_adapter(provider_override=None):
    provider_name = (provider_override or config.PROVIDER).lower().strip()
    adapter = _PROVIDER_ADAPTERS.get(provider_name)
    if adapter is None:
        raise Exception(f"Unknown PROVIDER: {provider_override or config.PROVIDER}")
    return adapter


def call_model(
    prompt: str,
    provider_override=None,
    max_output_tokens=None,
    timeout_handler=None,
    timeout_seconds=None,
    stop_checker=None,
) -> str:
    adapter = _resolve_provider_adapter(provider_override)
    return adapter.call_model(
        prompt=prompt,
        max_output_tokens=max_output_tokens or config.MAX_OUTPUT_TOKENS,
        timeout_seconds=timeout_seconds or config.MODEL_TIMEOUT,
        timeout_handler=timeout_handler,
        stop_checker=stop_checker,
    )


def list_models(provider_override=None, lmstudio_url=None, openai_api_key=None, lmstudio_api_key=None):
    adapter = _resolve_provider_adapter(provider_override)
    return adapter.list_models(
        lmstudio_url=lmstudio_url,
        openai_api_key=openai_api_key,
        lmstudio_api_key=lmstudio_api_key,
    )

import requests
import config


def _safe_extract_content(data):
    try:
        return data["choices"][0]["message"]["content"]
    except Exception:
        raise Exception(f"Bad model response shape: {data}")


def call_model(prompt: str) -> str:
    provider = config.PROVIDER.lower().strip()

    if provider == "lmstudio":
        payload = {
            "model": config.LMSTUDIO_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0
        }
        r = requests.post(config.LMSTUDIO_URL, json=payload, timeout=config.LMSTUDIO_TIMEOUT)
        r.raise_for_status()
        return _safe_extract_content(r.json())

    if provider == "openai":
        if not config.OPENAI_API_KEY.strip():
            raise Exception("OPENAI_API_KEY is empty in config.py / GUI field")

        headers = {
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": config.OPENAI_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0
        }
        r = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload, timeout=120)
        r.raise_for_status()
        return _safe_extract_content(r.json())

    raise Exception(f"Unknown PROVIDER: {config.PROVIDER}")

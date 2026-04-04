import requests
import config

def call_model(prompt):
    if config.PROVIDER == "lmstudio":
        return call_lmstudio(prompt)
    else:
        return call_openai(prompt)

def call_lmstudio(prompt):
    payload = {
        "model": config.LMSTUDIO_MODEL,
        "messages": [{"role": "user", "content": prompt}]
    }
    r = requests.post(config.LMSTUDIO_URL, json=payload)
    return r.json()["choices"][0]["message"]["content"]

def call_openai(prompt):
    headers = {"Authorization": f"Bearer {config.OPENAI_API_KEY}"}
    payload = {
        "model": config.OPENAI_MODEL,
        "messages": [{"role": "user", "content": prompt}]
    }
    r = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
    return r.json()["choices"][0]["message"]["content"]
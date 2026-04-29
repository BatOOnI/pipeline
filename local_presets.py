import os
from typing import Dict

from utils import read_json_file, write_json_file


def _normalize_name(name: str) -> str:
    return str(name or "").strip()


def _normalize_text(text: str) -> str:
    return str(text or "").strip()


def load_local_presets(path: str) -> Dict[str, str]:
    data = read_json_file(path, default={})
    if not isinstance(data, dict):
        return {}
    normalized = {}
    for key, value in data.items():
        name = _normalize_name(key)
        text = _normalize_text(value)
        if not name or not text:
            continue
        normalized[name] = text
    return dict(sorted(normalized.items(), key=lambda item: item[0].lower()))


def save_local_presets(path: str, presets: Dict[str, str]) -> Dict[str, str]:
    cleaned = {}
    for key, value in dict(presets or {}).items():
        name = _normalize_name(key)
        text = _normalize_text(value)
        if not name or not text:
            continue
        cleaned[name] = text
    cleaned = dict(sorted(cleaned.items(), key=lambda item: item[0].lower()))
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    write_json_file(path, cleaned)
    return cleaned


def upsert_local_preset(path: str, name: str, text: str) -> Dict[str, str]:
    preset_name = _normalize_name(name)
    preset_text = _normalize_text(text)
    if not preset_name:
        raise ValueError("Preset name is required.")
    if not preset_text:
        raise ValueError("Preset content is empty.")
    presets = load_local_presets(path)
    presets[preset_name] = preset_text
    return save_local_presets(path, presets)


def delete_local_preset(path: str, name: str) -> Dict[str, str]:
    preset_name = _normalize_name(name)
    presets = load_local_presets(path)
    if preset_name in presets:
        presets.pop(preset_name, None)
        return save_local_presets(path, presets)
    return presets

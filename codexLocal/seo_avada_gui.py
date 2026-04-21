import json
import os
import re
import threading
import time
import tkinter as tk
import base64
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests


PL_STOPWORDS = {
    "i",
    "oraz",
    "w",
    "na",
    "do",
    "z",
    "ze",
    "od",
    "po",
    "dla",
    "to",
    "jest",
    "sa",
    "sie",
    "nie",
    "ze",
    "jak",
    "o",
    "u",
    "pod",
    "nad",
    "przez",
    "czy",
    "ktory",
    "ktora",
    "ktore",
    "a",
    "ale",
    "lub",
}

EN_STOPWORDS = {
    "and",
    "the",
    "to",
    "for",
    "with",
    "in",
    "on",
    "of",
    "a",
    "an",
    "is",
    "are",
    "be",
    "this",
    "that",
    "from",
    "as",
    "or",
    "by",
    "at",
    "it",
    "your",
}

GARDEN_TERMS = [
    "garden room",
    "garden rooms",
    "timber frame",
    "cold roof",
    "permitted development",
    "slab foundation",
    "reinforced slab",
    "raised ventilated floor",
    "outdoor room",
    "southend-on-sea",
]

BATHROOM_TERMS = [
    "lazien",
    "bathroom",
    "kafel",
    "prysznic",
    "wanna",
    "umywal",
    "wc",
]


class SecureApiKeyStore:
    def __init__(self) -> None:
        home = Path(os.getenv("LOCALAPPDATA", str(Path.home())))
        self.store_file = home / "AvadaSeoGenerator" / "openai_api_key.dat"

    @staticmethod
    def _is_windows() -> bool:
        return os.name == "nt"

    @staticmethod
    def _crypt_protect(data: bytes) -> bytes:
        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32

        in_blob = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
        out_blob = DATA_BLOB()

        if not crypt32.CryptProtectData(
            ctypes.byref(in_blob),
            "OpenAI API Key",
            None,
            None,
            None,
            0,
            ctypes.byref(out_blob),
        ):
            raise RuntimeError("Nie udalo sie zaszyfrowac klucza (DPAPI).")

        try:
            result = ctypes.string_at(out_blob.pbData, out_blob.cbData)
            return result
        finally:
            kernel32.LocalFree(out_blob.pbData)

    @staticmethod
    def _crypt_unprotect(data: bytes) -> bytes:
        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32

        in_blob = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
        out_blob = DATA_BLOB()

        if not crypt32.CryptUnprotectData(
            ctypes.byref(in_blob),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(out_blob),
        ):
            raise RuntimeError("Nie udalo sie odszyfrowac klucza (DPAPI).")

        try:
            result = ctypes.string_at(out_blob.pbData, out_blob.cbData)
            return result
        finally:
            kernel32.LocalFree(out_blob.pbData)

    def save(self, api_key: str) -> None:
        key = api_key.strip()
        if not key:
            raise ValueError("Klucz API jest pusty.")

        self.store_file.parent.mkdir(parents=True, exist_ok=True)
        raw = key.encode("utf-8")
        if self._is_windows():
            payload = self._crypt_protect(raw)
        else:
            payload = raw
        self.store_file.write_text(base64.b64encode(payload).decode("ascii"), encoding="utf-8")

    def load(self) -> str:
        if not self.store_file.exists():
            return ""
        b64 = self.store_file.read_text(encoding="utf-8").strip()
        if not b64:
            return ""
        payload = base64.b64decode(b64)
        if self._is_windows():
            raw = self._crypt_unprotect(payload)
        else:
            raw = payload
        return raw.decode("utf-8")

    def clear(self) -> None:
        if self.store_file.exists():
            self.store_file.unlink()


@dataclass
class Placeholder:
    pid: str
    kind: str
    block_type: str
    field: str
    original: str
    suggested_words: int
    start: int
    end: int
    section_id: int


@dataclass
class ParseResult:
    placeholders: List[Placeholder]
    counts: Dict[str, int]


@dataclass
class StrategyResult:
    raw: Dict[str, object]
    sections: Dict[int, Dict[str, object]]


@dataclass
class PageContentResult:
    raw: Dict[str, object]
    mapped: Dict[str, str]
    missing_ids: List[str]


class AvadaParser:
    FUSION_TEXT_RE = re.compile(
        r"\[fusion_text\b[^\]]*\](.*?)\[/fusion_text\]",
        re.IGNORECASE | re.DOTALL,
    )
    FUSION_IMAGEFRAME_RE = re.compile(
        r"\[fusion_imageframe\b[^\]]*\](.*?)\[/fusion_imageframe\]",
        re.IGNORECASE | re.DOTALL,
    )
    FUSION_CONTENT_BOX_RE = re.compile(
        r"\[fusion_content_box\b([^\]]*)\](.*?)\[/fusion_content_box\]",
        re.IGNORECASE | re.DOTALL,
    )
    FUSION_IMAGE_RE = re.compile(
        r"\[fusion_image\b([^\]]*?)\s*/\]",
        re.IGNORECASE | re.DOTALL,
    )
    TITLE_ATTR_RE = re.compile(r'title\s*=\s*"([^"]*)"', re.IGNORECASE | re.DOTALL)
    IMAGE_ATTR_RE = re.compile(r'image\s*=\s*"([^"]*)"', re.IGNORECASE | re.DOTALL)
    CONTAINER_TOKEN_RE = re.compile(
        r"\[fusion_builder_container\b[^\]]*\]|\[/fusion_builder_container\]",
        re.IGNORECASE,
    )

    @staticmethod
    def _word_count(value: str) -> int:
        stripped = re.sub(r"\[[^\]]+\]", " ", value)
        stripped = re.sub(r"<[^>]+>", " ", stripped)
        words = re.findall(r"[A-Za-z0-9'-]+", stripped)
        return max(4, len(words))

    def _extract_container_ranges(self, raw: str) -> List[Tuple[int, int]]:
        ranges: List[Tuple[int, int]] = []
        stack: List[int] = []
        for token in self.CONTAINER_TOKEN_RE.finditer(raw):
            text = token.group(0).lower()
            if text.startswith("[fusion_builder_container"):
                stack.append(token.start())
            elif stack:
                start = stack.pop()
                ranges.append((start, token.end()))
        ranges.sort(key=lambda x: x[0])
        return ranges

    @staticmethod
    def _section_for_pos(pos: int, ranges: List[Tuple[int, int]]) -> int:
        for idx, (start, end) in enumerate(ranges, start=1):
            if start <= pos <= end:
                return idx
        return 0

    def parse(self, raw: str) -> ParseResult:
        placeholders: List[Placeholder] = []
        counts: Dict[str, int] = {
            "fusion_text": 0,
            "fusion_content_box_title": 0,
            "fusion_content_box_body": 0,
            "fusion_imageframe": 0,
            "fusion_image": 0,
        }

        sections = self._extract_container_ranges(raw)
        text_idx = 1
        img_idx = 1

        for m in self.FUSION_TEXT_RE.finditer(raw):
            section_id = self._section_for_pos(m.start(), sections)
            placeholders.append(
                Placeholder(
                    pid=f"TEXT_{text_idx:03d}",
                    kind="text",
                    block_type="fusion_text",
                    field="inner",
                    original=m.group(1).strip(),
                    suggested_words=self._word_count(m.group(1)),
                    start=m.start(1),
                    end=m.end(1),
                    section_id=section_id,
                )
            )
            counts["fusion_text"] += 1
            text_idx += 1

        for m in self.FUSION_CONTENT_BOX_RE.finditer(raw):
            section_id = self._section_for_pos(m.start(), sections)
            attrs = m.group(1)
            title_m = self.TITLE_ATTR_RE.search(attrs)
            if title_m:
                placeholders.append(
                    Placeholder(
                        pid=f"TEXT_{text_idx:03d}",
                        kind="text",
                        block_type="fusion_content_box",
                        field="title",
                        original=title_m.group(1).strip(),
                        suggested_words=max(3, self._word_count(title_m.group(1))),
                        start=m.start(1) + title_m.start(1),
                        end=m.start(1) + title_m.end(1),
                        section_id=section_id,
                    )
                )
                counts["fusion_content_box_title"] += 1
                text_idx += 1

            placeholders.append(
                Placeholder(
                    pid=f"TEXT_{text_idx:03d}",
                    kind="text",
                    block_type="fusion_content_box",
                    field="body",
                    original=m.group(2).strip(),
                    suggested_words=self._word_count(m.group(2)),
                    start=m.start(2),
                    end=m.end(2),
                    section_id=section_id,
                )
            )
            counts["fusion_content_box_body"] += 1
            text_idx += 1

        for m in self.FUSION_IMAGEFRAME_RE.finditer(raw):
            section_id = self._section_for_pos(m.start(), sections)
            placeholders.append(
                Placeholder(
                    pid=f"IMG_{img_idx:03d}",
                    kind="image",
                    block_type="fusion_imageframe",
                    field="inner",
                    original=m.group(1).strip(),
                    suggested_words=0,
                    start=m.start(1),
                    end=m.end(1),
                    section_id=section_id,
                )
            )
            counts["fusion_imageframe"] += 1
            img_idx += 1

        for m in self.FUSION_IMAGE_RE.finditer(raw):
            section_id = self._section_for_pos(m.start(), sections)
            attrs = m.group(1)
            image_attr = self.IMAGE_ATTR_RE.search(attrs)
            if not image_attr:
                continue

            placeholders.append(
                Placeholder(
                    pid=f"IMG_{img_idx:03d}",
                    kind="image",
                    block_type="fusion_image",
                    field="image",
                    original=image_attr.group(1).strip(),
                    suggested_words=0,
                    start=m.start(1) + image_attr.start(1),
                    end=m.start(1) + image_attr.end(1),
                    section_id=section_id,
                )
            )
            counts["fusion_image"] += 1
            img_idx += 1

        placeholders.sort(key=lambda p: p.start)
        return ParseResult(placeholders=placeholders, counts=counts)


class OpenAIClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        read_timeout_seconds: int = 60,
        is_cancelled: Optional[Callable[[], bool]] = None,
        on_timeout_decision: Optional[Callable[[int, int], bool]] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.api_key = api_key.strip()
        self.model = model.strip() or "gpt-4.1-mini"
        self.read_timeout_seconds = max(20, int(read_timeout_seconds))
        self.is_cancelled = is_cancelled
        self.on_timeout_decision = on_timeout_decision
        self.log = log

    def _log(self, message: str) -> None:
        if self.log:
            self.log(f"[OpenAIClient] {message}")

    def _request(self, prompt: str, temperature: float = 0.7) -> str:
        if not self.api_key:
            raise ValueError("Brak klucza OPENAI_API_KEY.")
        data = self._create_background_response(prompt, temperature)
        data = self._wait_until_completed(data)
        self._log(f"json_keys={list(data.keys())[:12]}")
        status = str(data.get("status", "")).lower()
        if status and status != "completed":
            self._log(f"final_status={status}")
            raise RuntimeError(f"Odpowiedz API nie zostala zakonczona poprawnie (status={status}).")

        output = data.get("output", [])
        parts: List[str] = []
        for item in output:
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    parts.append(content.get("text", ""))

        if parts:
            self._log(f"output_text_parts={len(parts)}")
            return "\n".join(parts).strip()

        fallback = data.get("output_text")
        if isinstance(fallback, str) and fallback.strip():
            self._log("using_output_text_fallback")
            return fallback.strip()

        self._log("no_text_in_response")
        raise RuntimeError("API nie zwrocilo tekstu.")

    def _create_background_response(self, prompt: str, temperature: float) -> Dict[str, Any]:
        attempt = 0
        while True:
            if self.is_cancelled and self.is_cancelled():
                raise RuntimeError("Operacja przerwana przez uzytkownika.")
            attempt += 1
            self._log(
                f"attempt={attempt} model={self.model} prompt_chars={len(prompt)} timeout_read={self.read_timeout_seconds}s background=true"
            )
            try:
                response = requests.post(
                    "https://api.openai.com/v1/responses",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "input": prompt,
                        "temperature": temperature,
                        "background": True,
                    },
                    timeout=(20, self.read_timeout_seconds),
                )
                self._log(
                    f"response_status={response.status_code} response_chars={len(response.text)} content_type={response.headers.get('content-type','')}"
                )
            except requests.Timeout:
                self._log(f"submit_timeout on attempt={attempt}")
                if self.is_cancelled and self.is_cancelled():
                    raise RuntimeError("Operacja przerwana przez uzytkownika.")
                if self.on_timeout_decision:
                    should_continue = self.on_timeout_decision(attempt, self.read_timeout_seconds)
                    if not should_continue:
                        raise RuntimeError("Przerwano po timeout na zadanie uzytkownika.")
                continue

            if response.status_code >= 400:
                self._log(f"http_error body_preview={response.text[:350]}")
                raise RuntimeError(f"Blad API {response.status_code}: {response.text[:500]}")
            data = response.json()
            rid = data.get("id", "")
            status = data.get("status", "")
            self._log(f"response_id={rid} status={status}")
            return data

    def _wait_until_completed(self, data: Dict[str, Any]) -> Dict[str, Any]:
        status = str(data.get("status", "")).lower()
        if status == "completed":
            return data

        response_id = str(data.get("id", "")).strip()
        if not response_id:
            raise RuntimeError("Brak response_id do monitorowania odpowiedzi.")

        poll_attempt = 0
        while True:
            if self.is_cancelled and self.is_cancelled():
                raise RuntimeError("Operacja przerwana przez uzytkownika.")
            poll_attempt += 1
            try:
                poll = requests.get(
                    f"https://api.openai.com/v1/responses/{response_id}",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                    },
                    timeout=(20, self.read_timeout_seconds),
                )
            except requests.Timeout:
                self._log(f"poll_timeout response_id={response_id} poll={poll_attempt}")
                if self.on_timeout_decision:
                    should_continue = self.on_timeout_decision(poll_attempt, self.read_timeout_seconds)
                    if not should_continue:
                        raise RuntimeError("Przerwano po timeout na zadanie uzytkownika.")
                continue

            if poll.status_code >= 400:
                self._log(f"poll_http_error status={poll.status_code} body_preview={poll.text[:350]}")
                raise RuntimeError(f"Blad API poll {poll.status_code}: {poll.text[:500]}")

            data = poll.json()
            status = str(data.get("status", "")).lower()
            self._log(f"poll response_id={response_id} poll={poll_attempt} status={status or 'unknown'}")

            if status == "completed":
                return data
            if status in {"failed", "cancelled", "expired", "incomplete"}:
                err = data.get("error")
                self._log(f"terminal_non_completed_status={status} error={err}")
                return data

            time.sleep(1.5)

    def _extract_json_object(self, raw: str) -> Dict[str, object]:
        cleaned = raw.strip()
        self._log(f"json_parse_input_chars={len(cleaned)}")
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
            cleaned = re.sub(r"```$", "", cleaned).strip()
        try:
            data = json.loads(cleaned)
        except Exception as exc:
            self._log(f"json_parse_error={exc} preview={cleaned[:500]}")
            raise
        if not isinstance(data, dict):
            self._log("json_not_object")
            raise ValueError("Oczekiwano obiektu JSON.")
        return data

    def generate_page_strategy(
        self,
        global_prompt: str,
        section_schema: List[Dict[str, object]],
    ) -> StrategyResult:
        prompt = (
            "Jestes strategiem SEO i content architectem. "
            "Przygotuj strategię strony i plan sekcji pod podany szablon AVADA. "
            "Zwroc WYLACZNIE poprawny JSON.\n\n"
            "Wymagany format:\n"
            "{\n"
            '  "page_topic": "string",\n'
            '  "seo_goal": "string",\n'
            '  "tone": "string",\n'
            '  "selling_points": ["..."],\n'
            '  "keywords_primary": ["..."],\n'
            '  "keywords_secondary": ["..."],\n'
            '  "sections": [\n'
            "    {\n"
            '      "section_id": 1,\n'
            '      "section_goal": "string",\n'
            '      "angle": "string",\n'
            '      "keywords": ["..."],\n'
            '      "cta_hint": "string"\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "Kazda sekcja musi miec section_id zgodne ze schema. "
            "Nie pomijaj sekcji, a jesli nie masz pewnosci, daj neutralny plan.\n\n"
            f"Kontekst strony:\n{global_prompt.strip()}\n\n"
            f"Schema sekcji:\n{json.dumps(section_schema, ensure_ascii=False)}"
        )
        raw = self._request(prompt, temperature=0.5)
        data = self._extract_json_object(raw)
        raw_sections = data.get("sections", [])
        section_map: Dict[int, Dict[str, object]] = {}
        if isinstance(raw_sections, list):
            for item in raw_sections:
                if not isinstance(item, dict):
                    continue
                sid = item.get("section_id")
                if isinstance(sid, int):
                    section_map[sid] = item
                elif isinstance(sid, str) and sid.isdigit():
                    section_map[int(sid)] = item
        return StrategyResult(raw=data, sections=section_map)

    def generate_page_content(
        self,
        global_prompt: str,
        strategy: Optional[StrategyResult],
        section_schema: List[Dict[str, object]],
        text_placeholders: List[Placeholder],
        language_mode: str = "auto",
        link_mode: str = "normal",
        format_mode: str = "normal",
    ) -> PageContentResult:
        placeholders_payload: List[Dict[str, object]] = []
        for ph in text_placeholders:
            placeholders_payload.append(
                {
                    "pid": ph.pid,
                    "section_id": ph.section_id,
                    "block_type": ph.block_type,
                    "field": ph.field,
                    "target_words": ph.suggested_words,
                    "source_excerpt": ph.original[:280],
                }
            )

        prompt = (
            "Wygeneruj tresc SEO dla CALEJ strony w JEDNYM przebiegu. "
            "Nie generuj blok po bloku. Zachowaj spojna semantyke calej strony.\n\n"
            "Zwróć WYŁĄCZNIE JSON w formacie:\n"
            "{\n"
            '  "page_title": "string",\n'
            '  "page_summary": "string",\n'
            '  "sections": [\n'
            "    {\n"
            '      "section_id": 1,\n'
            '      "section_goal": "string",\n'
            '      "placeholders": [\n'
            '        {"pid":"TEXT_001","content":"..."},\n'
            '        {"pid":"TEXT_002","content":"..."}\n'
            "      ]\n"
            "    }\n"
            "  ],\n"
            '  "placeholders": {\n'
            '    "TEXT_001": "...",\n'
            '    "TEXT_002": "..."\n'
            "  }\n"
            "}\n\n"
            "Reguly krytyczne:\n"
            "- Uwzglednij WSZYSTKIE placeholdery z listy.\n"
            "- Nie zwracaj markerow typu TEXT_001 jako tresci.\n"
            "- Nie mieszaj starego tematu z nowym.\n"
            "- Dlugosc tresci trzymaj blisko target_words.\n\n"
            "- Trzymaj cel i role sekcji zgodnie z section_kind i role_hint.\n"
            "- Jesli source_excerpt placeholdera zawiera <h1>/<h2>/<h3>, zwracaj tresc z odpowiednim tagiem heading HTML.\n"
            "- Dla heading-like blokow: pierwsza linia = naglowek, pusta linia, potem body.\n"
            "- Dla content_box: title i body musza byc semantycznie spojne.\n"
            f"- Tryb jezyka: {language_mode}\n"
            f"- Tryb linkow: {link_mode} (w strict uzywaj <a href=\"URL\">Anchor</a> dla internal links)\n"
            f"- Tryb formatowania: {format_mode}\n\n"
            f"Kontekst globalny:\n{global_prompt.strip()}\n\n"
            f"Strategia strony:\n{json.dumps(strategy.raw if strategy else {}, ensure_ascii=False)}\n\n"
            f"Schema sekcji:\n{json.dumps(section_schema, ensure_ascii=False)}\n\n"
            f"Lista placeholderow:\n{json.dumps(placeholders_payload, ensure_ascii=False)}"
        )

        raw = self._request(prompt, temperature=0.55)
        data = self._extract_json_object(raw)

        mapped: Dict[str, str] = {}
        root_map = data.get("placeholders")
        if isinstance(root_map, dict):
            for k, v in root_map.items():
                if isinstance(v, str):
                    mapped[str(k)] = v.strip()

        sections = data.get("sections")
        if isinstance(sections, list):
            for section in sections:
                if not isinstance(section, dict):
                    continue
                items = section.get("placeholders")
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    pid = item.get("pid")
                    content = item.get("content")
                    if isinstance(pid, str) and isinstance(content, str):
                        mapped[pid] = content.strip()

        missing = [ph.pid for ph in text_placeholders if ph.pid not in mapped or not mapped.get(ph.pid)]
        return PageContentResult(raw=data, mapped=mapped, missing_ids=missing)

    def generate_text(
        self,
        global_prompt: str,
        placeholder: Placeholder,
        optional_prompt: str,
        strategy: Optional[StrategyResult] = None,
        section_schema_entry: Optional[Dict[str, object]] = None,
        language_mode: str = "auto",
        link_mode: str = "normal",
        format_mode: str = "normal",
    ) -> str:
        strategy_payload = strategy.raw if strategy else {}
        section_plan = strategy.sections.get(placeholder.section_id, {}) if strategy else {}
        schema_entry = section_schema_entry or {}
        prompt = (
            "Napisz tekst SEO po polsku do sekcji strony. "
            "Dbaj o spojna tematyke z calym promptem strony i NIE mieszaj tematow. "
            "Nie zostawiaj markerow typu TEXT_001. "
            f"Docelowa dlugosc: okolo {placeholder.suggested_words} slow (+/- 15%).\n\n"
            f"Kontekst globalny:\n{global_prompt.strip()}\n\n"
            f"Strategia strony (JSON):\n{json.dumps(strategy_payload, ensure_ascii=False)}\n\n"
            f"Plan sekcji (JSON):\n{json.dumps(section_plan, ensure_ascii=False)}\n\n"
            f"Schema sekcji (JSON):\n{json.dumps(schema_entry, ensure_ascii=False)}\n\n"
            f"Typ bloku: {placeholder.block_type}, pole: {placeholder.field}, id: {placeholder.pid}\n"
            f"Tresc zrodlowa:\n{placeholder.original.strip()}\n\n"
            f"Tryb jezyka: {language_mode}\n"
            f"Tryb linkow: {link_mode} (strict => <a href=\"URL\">Anchor</a> dla internal links)\n"
            f"Tryb formatowania: {format_mode} (avada-strict => heading line + blank line + body)\n\n"
            "Jesli tresc zrodlowa zawiera <h1>/<h2>/<h3>, zachowaj heading HTML w wyniku.\n\n"
            f"Dodatkowe instrukcje:\n{optional_prompt.strip() or '(brak)'}\n\n"
            "Zwroc tylko finalny tekst do podmiany."
        )
        return self._request(prompt)

    def generate_image_metadata(
        self,
        global_prompt: str,
        placeholder_id: str,
        image_reference: str,
        optional_prompt: str,
    ) -> Dict[str, str]:
        prompt = (
            "Tworzysz metadane SEO obrazka. "
            "Zwroc WYLACZNIE JSON: "
            '{"filename":"...","alt":"...","description":"..."}. '
            "filename: ascii, male litery, myslniki, bez rozszerzenia. "
            "alt: max okolo 140 znakow. "
            "description: 1-2 zdania po polsku.\n\n"
            f"Kontekst strony:\n{global_prompt.strip()}\n\n"
            f"Placeholder: {placeholder_id}\n"
            f"Zrodlo obrazka: {image_reference.strip() or '(brak)'}\n"
            f"Dodatkowe wytyczne: {optional_prompt.strip() or '(brak)'}"
        )
        raw = self._request(prompt, temperature=0.4)

        data = self._extract_json_object(raw)

        return {
            "filename": str(data.get("filename", "")).strip(),
            "alt": str(data.get("alt", "")).strip(),
            "description": str(data.get("description", "")).strip(),
        }


class PlaceholderRow:
    def __init__(self, parent: tk.Widget, placeholder: Placeholder, row: int) -> None:
        self.placeholder = placeholder

        block_label = f"{placeholder.pid} [{placeholder.block_type}:{placeholder.field}]"
        container = ttk.LabelFrame(parent, text=block_label)
        container.grid(row=row, column=0, sticky="ew", padx=8, pady=6)
        container.columnconfigure(1, weight=1)

        if placeholder.kind == "text":
            ttk.Label(
                container,
                text=f"Sugerowana dlugosc: {placeholder.suggested_words} slow",
            ).grid(row=0, column=0, columnspan=3, sticky="w", padx=6, pady=(6, 4))

            ttk.Label(container, text="Dodatkowy prompt:").grid(row=1, column=0, sticky="w", padx=6)
            self.extra_prompt = tk.Text(container, height=2, width=40)
            self.extra_prompt.grid(row=1, column=1, sticky="ew", padx=6, pady=4)
            self.single_button = ttk.Button(container, text="Zrob tekst")
            self.single_button.grid(row=1, column=2, sticky="e", padx=6)

            ttk.Label(container, text="Wynik:").grid(row=2, column=0, sticky="nw", padx=6)
            self.result = tk.Text(container, height=6, width=90)
            self.result.grid(row=2, column=1, columnspan=2, sticky="ew", padx=6, pady=(0, 6))
            self.result.insert("1.0", placeholder.original.strip())
        else:
            ttk.Label(container, text="Sciezka/URL zdjecia:").grid(
                row=0, column=0, sticky="w", padx=6, pady=(6, 2)
            )
            self.image_path = tk.StringVar(value=placeholder.original.strip())
            self.path_entry = ttk.Entry(container, textvariable=self.image_path)
            self.path_entry.grid(row=0, column=1, sticky="ew", padx=6, pady=(6, 2))
            self.browse_button = ttk.Button(container, text="Wybierz zdjecie")
            self.browse_button.grid(row=0, column=2, sticky="e", padx=6, pady=(6, 2))

            ttk.Label(container, text="Prompt metadanych:").grid(row=1, column=0, sticky="w", padx=6, pady=2)
            self.image_prompt = tk.Text(container, height=2, width=40)
            self.image_prompt.grid(row=1, column=1, sticky="ew", padx=6, pady=2)
            self.image_meta_button = ttk.Button(container, text="Generuj metadane")
            self.image_meta_button.grid(row=1, column=2, sticky="e", padx=6, pady=2)

            ttk.Label(container, text="Nazwa pliku:").grid(row=2, column=0, sticky="w", padx=6, pady=2)
            self.image_filename = tk.StringVar(value="")
            ttk.Entry(container, textvariable=self.image_filename).grid(
                row=2, column=1, columnspan=2, sticky="ew", padx=6, pady=2
            )

            ttk.Label(container, text="ALT:").grid(row=3, column=0, sticky="w", padx=6, pady=2)
            self.image_alt = tk.StringVar(value="")
            ttk.Entry(container, textvariable=self.image_alt).grid(
                row=3, column=1, columnspan=2, sticky="ew", padx=6, pady=2
            )

            ttk.Label(container, text="Opis:").grid(row=4, column=0, sticky="nw", padx=6, pady=2)
            self.image_description = tk.Text(container, height=3, width=60)
            self.image_description.grid(
                row=4, column=1, columnspan=2, sticky="ew", padx=6, pady=(2, 6)
            )

    def get_extra_prompt(self) -> str:
        if self.placeholder.kind != "text":
            return ""
        return self.extra_prompt.get("1.0", "end").strip()

    def set_result(self, text: str) -> None:
        if self.placeholder.kind != "text":
            return
        self.result.delete("1.0", "end")
        self.result.insert("1.0", text)

    def get_result(self) -> str:
        if self.placeholder.kind != "text":
            return ""
        return self.result.get("1.0", "end").strip()

    def get_image_value(self) -> str:
        if self.placeholder.kind != "image":
            return ""
        return self.image_path.get().strip()

    def get_image_prompt(self) -> str:
        if self.placeholder.kind != "image":
            return ""
        return self.image_prompt.get("1.0", "end").strip()

    def set_image_metadata(self, filename: str, alt: str, description: str) -> None:
        if self.placeholder.kind != "image":
            return
        self.image_filename.set(filename)
        self.image_alt.set(alt)
        self.image_description.delete("1.0", "end")
        self.image_description.insert("1.0", description)

    def get_image_metadata(self) -> Dict[str, str]:
        if self.placeholder.kind != "image":
            return {}
        return {
            "filename": self.image_filename.get().strip(),
            "alt": self.image_alt.get().strip(),
            "description": self.image_description.get("1.0", "end").strip(),
        }


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("AVADA SEO Placeholder Generator")
        self.geometry("1380x920")

        self.parser = AvadaParser()
        self.template_raw = ""
        self.parse_result: Optional[ParseResult] = None
        self.placeholders: List[Placeholder] = []
        self.rows: Dict[str, PlaceholderRow] = {}
        self.last_scan_report: Dict[str, int] = {}
        self.page_strategy: Optional[StrategyResult] = None
        self.page_content: Optional[PageContentResult] = None
        self.section_schema: List[Dict[str, object]] = []
        self.last_mapping_report: Dict[str, object] = {}
        self.api_key_store = SecureApiKeyStore()
        self._operation_seq = 0
        self._operations: Dict[int, Dict[str, object]] = {}
        self._logs: List[str] = []
        self._log_window: Optional[tk.Toplevel] = None
        self._log_text: Optional[tk.Text] = None
        self.internal_domains: List[str] = []

        self._build_ui()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Plik szablonu:").grid(row=0, column=0, sticky="w")
        self.template_path = tk.StringVar(value=str(Path.cwd() / "szablon.txt"))
        ttk.Entry(top, textvariable=self.template_path).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(top, text="Wybierz", command=self.pick_template).grid(row=0, column=2)
        ttk.Button(top, text="Wczytaj szablon", command=self.load_template).grid(row=0, column=3, padx=(6, 0))
        ttk.Button(top, text="Raport skanowania", command=self.show_scan_report).grid(row=0, column=4, padx=(6, 0))
        ttk.Button(top, text="Podglad strategii", command=self.show_strategy_preview).grid(row=0, column=5, padx=(6, 0))
        ttk.Button(top, text="Live logi", command=self.show_live_logs).grid(row=0, column=6, padx=(6, 0))

        ttk.Label(top, text="Plik wyjsciowy:").grid(row=1, column=0, sticky="w", pady=6)
        self.output_path = tk.StringVar(value=str(Path.cwd() / "output_szablon.txt"))
        ttk.Entry(top, textvariable=self.output_path).grid(row=1, column=1, sticky="ew", padx=6)
        ttk.Button(top, text="Zapisz jako", command=self.pick_output).grid(row=1, column=2)
        ttk.Button(top, text="Waliduj output", command=self.validate_existing_output).grid(row=1, column=3, padx=(6, 0))

        ttk.Label(top, text="Raport porownania:").grid(row=2, column=0, sticky="w", pady=2)
        self.compare_path = tk.StringVar(value=str(Path.cwd() / "output_szablon.txt"))
        ttk.Entry(top, textvariable=self.compare_path).grid(row=2, column=1, sticky="ew", padx=6)
        ttk.Button(top, text="Wybierz", command=self.pick_compare).grid(row=2, column=2)
        ttk.Button(top, text="Porownaj zmiany", command=self.compare_outputs).grid(row=2, column=3, padx=(6, 0))

        ttk.Label(top, text="Plik metadanych obrazow:").grid(row=3, column=0, sticky="w", pady=(2, 0))
        self.image_metadata_path = tk.StringVar(value=str(Path.cwd() / "image_metadata.json"))
        ttk.Entry(top, textvariable=self.image_metadata_path).grid(row=3, column=1, sticky="ew", padx=6, pady=(2, 0))
        ttk.Button(top, text="Zapisz jako", command=self.pick_metadata_output).grid(row=3, column=2, pady=(2, 0))

        creds = ttk.Frame(self)
        creds.grid(row=1, column=0, sticky="ew", padx=10, pady=4)
        creds.columnconfigure(1, weight=1)
        creds.columnconfigure(3, weight=1)

        ttk.Label(creds, text="OpenAI API Key:").grid(row=0, column=0, sticky="w")
        self.api_key = tk.StringVar(value=os.getenv("OPENAI_API_KEY", ""))
        ttk.Entry(creds, textvariable=self.api_key, show="*").grid(row=0, column=1, sticky="ew", padx=(6, 12))
        ttk.Button(creds, text="Zapisz klucz", command=self.save_api_key_secure).grid(row=0, column=4, padx=(6, 0))
        ttk.Button(creds, text="Wczytaj klucz", command=self.load_api_key_secure).grid(row=0, column=5, padx=(6, 0))
        ttk.Button(creds, text="Usun klucz", command=self.clear_api_key_secure).grid(row=0, column=6, padx=(6, 0))

        ttk.Label(creds, text="Model:").grid(row=0, column=2, sticky="w")
        self.model = tk.StringVar(value="gpt-4.1-mini")
        self.model_combo = ttk.Combobox(
            creds,
            textvariable=self.model,
            values=["gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini", "gpt-4o", "o4-mini"],
        )
        self.model_combo.grid(row=0, column=3, sticky="ew", padx=6)
        ttk.Button(creds, text="Aktualizuj modele", command=self.refresh_models).grid(
            row=0, column=7, padx=(6, 0)
        )
        ttk.Label(creds, text="Tryb:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.generation_mode = tk.StringVar(value="text-only")
        ttk.Combobox(
            creds,
            textvariable=self.generation_mode,
            values=["text-only", "text+image"],
            state="readonly",
            width=16,
        ).grid(row=1, column=1, sticky="w", pady=(6, 0))
        ttk.Label(creds, text="Jezyk:").grid(row=1, column=2, sticky="w", pady=(6, 0))
        self.language_mode = tk.StringVar(value="auto")
        ttk.Combobox(
            creds,
            textvariable=self.language_mode,
            values=["auto", "english-only", "polish-only"],
            state="readonly",
            width=16,
        ).grid(row=1, column=3, sticky="w", pady=(6, 0))
        ttk.Label(creds, text="Linki:").grid(row=1, column=4, sticky="w", pady=(6, 0))
        self.link_mode = tk.StringVar(value="strict-html-internal")
        ttk.Combobox(
            creds,
            textvariable=self.link_mode,
            values=["strict-html-internal", "normal"],
            state="readonly",
            width=20,
        ).grid(row=1, column=5, sticky="w", pady=(6, 0))
        ttk.Label(creds, text="Format:").grid(row=1, column=6, sticky="w", pady=(6, 0))
        self.format_mode = tk.StringVar(value="avada-strict")
        ttk.Combobox(
            creds,
            textvariable=self.format_mode,
            values=["avada-strict", "normal"],
            state="readonly",
            width=14,
        ).grid(row=1, column=7, sticky="w", pady=(6, 0))

        prompt_frame = ttk.LabelFrame(self, text="Glowny prompt strony")
        prompt_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=4)
        prompt_frame.columnconfigure(0, weight=1)
        prompt_holder = ttk.Frame(prompt_frame)
        prompt_holder.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        prompt_holder.columnconfigure(0, weight=1)
        prompt_holder.rowconfigure(0, weight=1)
        self.global_prompt = tk.Text(prompt_holder, height=8, wrap="word")
        self.global_prompt.grid(row=0, column=0, sticky="nsew")
        prompt_scroll = ttk.Scrollbar(prompt_holder, orient="vertical", command=self.global_prompt.yview)
        prompt_scroll.grid(row=0, column=1, sticky="ns")
        self.global_prompt.configure(yscrollcommand=prompt_scroll.set)
        self.global_prompt.insert(
            "1.0",
            "Ta strona jest o ... (np. remontach lazienek w Southend, przewagach firmy i grupie docelowej).",
        )

        actions = ttk.Frame(self)
        actions.grid(row=3, column=0, sticky="ew", padx=10, pady=4)

        self.strategy_button = ttk.Button(actions, text="Etap 1: Generuj strategie", command=self.generate_strategy)
        self.strategy_button.pack(side="left")

        self.bulk_button = ttk.Button(actions, text="Etap 2: Generuj content", command=self.generate_all)
        self.bulk_button.pack(side="left", padx=8)

        self.build_button = ttk.Button(actions, text="Etap 3: Generuj finalny plik", command=self.build_output)
        self.build_button.pack(side="left", padx=8)

        self.bulk_image_meta_button = ttk.Button(
            actions,
            text="Etap 4: Metadane obrazow",
            command=self.generate_all_image_metadata,
        )
        self.bulk_image_meta_button.pack(side="left", padx=8)

        self.save_meta_button = ttk.Button(actions, text="Etap 5: Zapisz metadane", command=self.save_image_metadata)
        self.save_meta_button.pack(side="left", padx=8)

        self.status = tk.StringVar(value="Wczytaj szablon, aby rozpoczac.")
        ttk.Label(actions, textvariable=self.status).pack(side="left", padx=12)
        self.strategy_status = tk.StringVar(value="Strategia: brak")
        ttk.Label(actions, textvariable=self.strategy_status).pack(side="left", padx=12)

        holder = ttk.Frame(self)
        holder.grid(row=4, column=0, sticky="nsew", padx=10, pady=(4, 10))
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(holder)
        self.canvas.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(holder, orient="vertical", command=self.canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.scroll_frame = ttk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw")
        self.scroll_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.load_api_key_secure(silent=True)

    def save_api_key_secure(self) -> None:
        try:
            self.api_key_store.save(self.api_key.get())
            messagebox.showinfo("Sukces", "Klucz API zapisany bezpiecznie w systemie (DPAPI).")
        except Exception as exc:
            messagebox.showerror("Blad", f"Nie udalo sie zapisac klucza: {exc}")

    def load_api_key_secure(self, silent: bool = False) -> None:
        try:
            key = self.api_key_store.load()
            if key:
                self.api_key.set(key)
                if not silent:
                    messagebox.showinfo("Sukces", "Klucz API zostal wczytany.")
            elif not silent:
                messagebox.showinfo("Info", "Brak zapisanego klucza API.")
        except Exception as exc:
            if not silent:
                messagebox.showerror("Blad", f"Nie udalo sie wczytac klucza: {exc}")

    def clear_api_key_secure(self) -> None:
        try:
            self.api_key_store.clear()
            self.api_key.set("")
            messagebox.showinfo("Sukces", "Zapisany klucz API zostal usuniety.")
        except Exception as exc:
            messagebox.showerror("Blad", f"Nie udalo sie usunac klucza: {exc}")

    def refresh_models(self) -> None:
        api_key = self.api_key.get().strip()
        if not api_key:
            messagebox.showwarning("Brak klucza", "Najpierw wpisz lub wczytaj OpenAI API Key.")
            return
        self.status.set("Pobieranie listy modeli...")
        op_id = self._begin_operation("Aktualizacja modeli")

        def job() -> None:
            try:
                response = requests.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=(20, 60),
                )
                if response.status_code >= 400:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text[:250]}")
                data = response.json()
                items = data.get("data", [])
                model_ids = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    mid = item.get("id")
                    if not isinstance(mid, str):
                        continue
                    if mid.startswith(("gpt", "o")):
                        model_ids.append(mid)
                model_ids = sorted(set(model_ids))
                if not model_ids:
                    raise RuntimeError("Nie znaleziono modeli gpt/o na koncie.")

                def apply_models() -> None:
                    self.model_combo["values"] = model_ids
                    if self.model.get() not in model_ids:
                        self.model.set(model_ids[0])
                    self.status.set(f"Zaktualizowano modele ({len(model_ids)}).")

                self.after(0, apply_models)
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Blad modeli", str(exc)))
                self.after(0, lambda: self.status.set("Nie udalo sie pobrac modeli."))
            finally:
                self.after(0, lambda: self._end_operation(op_id))

        threading.Thread(target=job, daemon=True).start()

    def pick_template(self) -> None:
        path = filedialog.askopenfilename(
            title="Wybierz szablon AVADA",
            filetypes=[("Tekst", "*.txt *.html *.php"), ("Wszystkie", "*.*")],
        )
        if path:
            self.template_path.set(path)

    def pick_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Zapisz plik wynikowy",
            defaultextension=".txt",
            filetypes=[("Tekst", "*.txt"), ("Wszystkie", "*.*")],
        )
        if path:
            self.output_path.set(path)

    def pick_compare(self) -> None:
        path = filedialog.askopenfilename(
            title="Wybierz plik do porownania",
            filetypes=[("Tekst", "*.txt *.html *.php"), ("Wszystkie", "*.*")],
        )
        if path:
            self.compare_path.set(path)

    def pick_metadata_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Zapisz metadane obrazow",
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("Wszystkie", "*.*")],
        )
        if path:
            self.image_metadata_path.set(path)

    @staticmethod
    def _read_text(path: Path) -> str:
        for encoding in ("utf-8", "cp1250", "latin-1"):
            try:
                return path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
        return path.read_text(errors="replace")

    @staticmethod
    def _normalize_text(value: str) -> str:
        val = value.replace("\r\n", "\n").replace("\r", "\n").strip().lower()
        return re.sub(r"\s+", " ", val)

    @staticmethod
    def _count_words(value: str) -> int:
        return len(re.findall(r"[A-Za-z0-9'-]+", value))

    @staticmethod
    def _tokenize_keywords(value: str) -> List[str]:
        tokens = [t.lower() for t in re.findall(r"[A-Za-z0-9'-]{3,}", value)]
        return [t for t in tokens if t not in PL_STOPWORDS and t not in EN_STOPWORDS]

    def _detect_target_topic(self) -> str:
        prompt = self.global_prompt.get("1.0", "end").lower()
        if any(term in prompt for term in BATHROOM_TERMS):
            return "bathroom"
        if any(term in prompt for term in GARDEN_TERMS):
            return "garden"
        return "unknown"

    def _required_language(self) -> str:
        mode = self.language_mode.get().strip().lower()
        if mode in ("english-only", "polish-only"):
            return "en" if mode == "english-only" else "pl"
        prompt = self.global_prompt.get("1.0", "end").lower()
        if any(x in prompt for x in ["english only", "only english", "write in english"]):
            return "en"
        if any(x in prompt for x in ["tylko po polsku", "po polsku", "polish only"]):
            return "pl"
        return "any"

    @staticmethod
    def _normalize_spacing(text: str) -> str:
        value = text.replace("\r\n", "\n").replace("\r", "\n")
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()

    @staticmethod
    def _has_heading_tag(text: str) -> bool:
        return bool(re.search(r"<h([1-6])\b[^>]*>.*?</h\1>", text, flags=re.IGNORECASE | re.DOTALL))

    @staticmethod
    def _first_heading_tag_meta(text: str) -> Optional[Tuple[str, str]]:
        m = re.search(r"<h([1-6])(\b[^>]*)>.*?</h\1>", text, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            return None
        level = m.group(1)
        attrs = m.group(2) or ""
        return level, attrs

    @staticmethod
    def _strip_tags(text: str) -> str:
        return re.sub(r"<[^>]+>", "", text).strip()

    @staticmethod
    def _source_heading_word_count(text: str) -> int:
        m = re.search(r"<h([1-6])\b[^>]*>(.*?)</h\1>", text, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            return 0
        inner = App._strip_tags(m.group(2))
        return len(re.findall(r"[A-Za-z0-9'-]+", inner))

    @staticmethod
    def _has_heading_like_pattern(text: str) -> bool:
        if App._has_heading_tag(text):
            return True
        lines = text.replace("\r\n", "\n").split("\n")
        if len(lines) < 3:
            return False
        first = lines[0].strip()
        second = lines[1].strip()
        return bool(first and second == "" and len(first.split()) <= 14)

    def _preserve_heading_layout(self, source: str, generated: str) -> str:
        if not self._has_heading_like_pattern(source):
            return self._normalize_spacing(generated)
        txt = self._normalize_spacing(generated)
        if self._has_heading_tag(source):
            if self._has_heading_tag(txt):
                txt = re.sub(r"(</h[1-6]>)\s*(?=\S)", r"\1\n\n", txt, count=1, flags=re.IGNORECASE)
                return txt.strip()

            source_heading = self._first_heading_tag_meta(source) or ("2", "")
            level, attrs = source_heading
            lines = [x.strip() for x in txt.split("\n") if x.strip()]
            if not lines:
                return txt
            heading_text = self._strip_tags(lines[0]) or "Sekcja"
            body = "\n".join(lines[1:]).strip()
            if not body:
                wc = self._count_words(heading_text)
                if wc > 16:
                    target_h_words = self._source_heading_word_count(source) or 8
                    target_h_words = max(4, min(12, target_h_words))
                    words = heading_text.split()
                    heading_text = " ".join(words[:target_h_words]).strip(" .,;:-")
                    body = " ".join(words[target_h_words:]).strip()
            open_tag = f"<h{level}{attrs}>"
            close_tag = f"</h{level}>"
            if body:
                return f"{open_tag}{heading_text}{close_tag}\n\n{body}".strip()
            return f"{open_tag}{heading_text}{close_tag}"

        lines = [x.strip() for x in txt.split("\n") if x.strip()]
        if not lines:
            return txt
        if len(lines) == 1:
            return lines[0]
        heading = lines[0]
        body = "\n".join(lines[1:])
        return f"{heading}\n\n{body}".strip()

    def _enforce_internal_html_links(self, text: str) -> str:
        if self.link_mode.get() != "strict-html-internal":
            return text

        def is_internal(url: str) -> bool:
            if url.startswith("/"):
                return True
            for d in self.internal_domains:
                if d and d in url:
                    return True
            return False

        # Markdown link -> HTML anchor for internal targets.
        def repl_md(m: re.Match[str]) -> str:
            label = m.group(1).strip()
            url = m.group(2).strip()
            if is_internal(url):
                return f'<a href="{url}">{label}</a>'
            return m.group(0)

        out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", repl_md, text)
        return out

    def _postprocess_generated_text(self, ph: Placeholder, text: str) -> str:
        out = text.strip()
        if self.format_mode.get() == "avada-strict":
            out = self._preserve_heading_layout(ph.original, out)
        else:
            out = self._normalize_spacing(out)
        out = self._enforce_internal_html_links(out)
        return out

    def load_template(self) -> None:
        path = Path(self.template_path.get().strip())
        if not path.exists():
            messagebox.showerror("Blad", f"Nie znaleziono pliku: {path}")
            return

        self.template_raw = self._read_text(path)
        domains = re.findall(r"https?://([^/\s\"']+)", self.template_raw, flags=re.IGNORECASE)
        self.internal_domains = sorted(set(domains))
        self.parse_result = self.parser.parse(self.template_raw)
        self.placeholders = self.parse_result.placeholders
        self.last_scan_report = self.parse_result.counts.copy()
        self.page_strategy = None
        self.page_content = None
        self.last_mapping_report = {}
        self.section_schema = self._build_section_schema()
        self.strategy_status.set("Strategia: brak (wygeneruj Etap 1)")

        self.rows.clear()
        for child in self.scroll_frame.winfo_children():
            child.destroy()

        for idx, ph in enumerate(self.placeholders):
            row = PlaceholderRow(self.scroll_frame, ph, idx)
            self.rows[ph.pid] = row
            if ph.kind == "text":
                row.single_button.configure(command=lambda p=ph.pid: self.generate_one(p))
            else:
                row.browse_button.configure(command=lambda r=row: self.pick_image_for_row(r))
                row.image_meta_button.configure(command=lambda p=ph.pid: self.generate_one_image_metadata(p))

        text_count = len([p for p in self.placeholders if p.kind == "text"])
        img_count = len([p for p in self.placeholders if p.kind == "image"])
        self.status.set(f"Wczytano {text_count} blokow tekstu i {img_count} blokow obrazow.")

    def _build_section_schema(self) -> List[Dict[str, object]]:
        schema: List[Dict[str, object]] = []
        by_section: Dict[int, List[Placeholder]] = {}
        for ph in self.placeholders:
            by_section.setdefault(ph.section_id, []).append(ph)

        for section_id in sorted(by_section.keys()):
            items = sorted(by_section[section_id], key=lambda x: x.start)
            text_items = [p for p in items if p.kind == "text"]
            image_items = [p for p in items if p.kind == "image"]
            preview = text_items[0].original[:140].replace("\n", " ") if text_items else ""

            content_box_titles = [p.pid for p in text_items if p.block_type == "fusion_content_box" and p.field == "title"]
            content_box_bodies = [p.pid for p in text_items if p.block_type == "fusion_content_box" and p.field == "body"]
            short_texts = [p for p in text_items if p.suggested_words <= 14]
            faq_like = [p for p in text_items if "?" in p.original]

            if content_box_titles or content_box_bodies:
                section_kind = "content_box_section"
            elif len(faq_like) >= 2:
                section_kind = "faq_section"
            elif len(text_items) >= 3 and len(image_items) == 0:
                section_kind = "multi_column_text_section"
            elif len(text_items) == 1 and len(image_items) == 0:
                section_kind = "single_text_section"
            elif len(text_items) >= 1 and len(image_items) >= 1:
                section_kind = "text_image_section"
            elif len(text_items) == 0 and len(image_items) > 0:
                section_kind = "image_section"
            else:
                section_kind = "generic_section"

            role_hint = "general"
            if section_id == 1:
                role_hint = "page_intro"
            elif section_kind == "faq_section":
                role_hint = "faq"
            elif section_kind == "content_box_section":
                role_hint = "selling_points"
            elif len(short_texts) >= 1 and len(text_items) >= 1 and section_id > 1:
                role_hint = "heading_plus_body"
            elif section_id == max(by_section.keys()):
                role_hint = "cta_or_closing"

            schema.append(
                {
                    "section_id": section_id,
                    "section_key": f"section_{section_id:03d}",
                    "section_kind": section_kind,
                    "role_hint": role_hint,
                    "placeholder_ids": [p.pid for p in text_items],
                    "image_placeholder_ids": [p.pid for p in image_items],
                    "block_types": [f"{p.block_type}:{p.field}" for p in text_items],
                    "text_blocks": len(text_items),
                    "images": len(image_items),
                    "heading_candidates": [p.pid for p in short_texts[:2]],
                    "content_box_items": [
                        {"title_pid": t, "body_pid": content_box_bodies[idx] if idx < len(content_box_bodies) else ""}
                        for idx, t in enumerate(content_box_titles)
                    ],
                    "source_preview": preview,
                    "text_placeholders": [
                        {
                            "pid": p.pid,
                            "block_type": p.block_type,
                            "field": p.field,
                            "target_words": p.suggested_words,
                            "source_excerpt": p.original[:140].replace("\n", " "),
                        }
                        for p in text_items
                    ],
                }
            )
        return schema

    def _schema_by_section_id(self) -> Dict[int, Dict[str, object]]:
        return {int(x["section_id"]): x for x in self.section_schema if "section_id" in x}

    def generate_strategy(self) -> None:
        if not self.placeholders:
            messagebox.showwarning("Brak", "Najpierw wczytaj szablon.")
            return

        self.strategy_button.configure(state="disabled")
        self.status.set("Etap 1: generowanie strategii strony...")
        op_id = self._begin_operation("Etap 1: Generowanie strategii")

        def job() -> None:
            try:
                strategy = self._client(op_id).generate_page_strategy(
                    global_prompt=self.global_prompt.get("1.0", "end").strip(),
                    section_schema=self.section_schema,
                )
                if self._is_operation_cancelled(op_id):
                    self.after(0, lambda: self.status.set("Etap 1 przerwany."))
                    return
                self.page_content = None
                missing = []
                for section in self.section_schema:
                    sid = int(section["section_id"])
                    if sid not in strategy.sections:
                        missing.append(sid)
                self.page_strategy = strategy

                status_msg = f"Strategia gotowa. Sekcje z planem: {len(strategy.sections)}/{len(self.section_schema)}"
                if missing:
                    status_msg += f" (fallback dla: {', '.join(str(x) for x in missing)})"

                self.after(0, lambda: self.strategy_status.set(status_msg))
                self.after(0, lambda: self.status.set("Etap 1 zakonczony. Mozesz uruchomic Etap 2."))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Blad strategii", str(exc)))
                self.after(0, lambda: self.status.set("Blad Etapu 1."))
            finally:
                self.after(0, lambda: self.strategy_button.configure(state="normal"))
                self.after(0, lambda: self._end_operation(op_id))

        threading.Thread(target=job, daemon=True).start()

    def show_scan_report(self) -> None:
        if not self.last_scan_report:
            messagebox.showinfo("Raport", "Najpierw wczytaj szablon.")
            return

        lines = ["Wykryte elementy AVADA:"]
        for key, value in self.last_scan_report.items():
            lines.append(f"- {key}: {value}")
        lines.append(f"- Lacznie placeholderow: {len(self.placeholders)}")
        messagebox.showinfo("Raport skanowania", "\n".join(lines))

    def show_strategy_preview(self) -> None:
        win = tk.Toplevel(self)
        win.title("Podglad strategii i mapowania")
        win.geometry("1100x760")

        summary_lines = [
            f"Placeholdery lacznie: {len(self.placeholders)}",
            f"Sekcje inferred: {len(self.section_schema)}",
        ]
        if self.page_strategy:
            summary_lines.append(f"Sekcje w strategii: {len(self.page_strategy.sections)}")
        else:
            summary_lines.append("Sekcje w strategii: brak (uruchom Etap 1)")
        if self.page_content:
            summary_lines.append(f"Wygenerowane klucze placeholderow: {len(self.page_content.mapped)}")
            summary_lines.append(f"Braki mapowania: {len(self.page_content.missing_ids)}")
        else:
            summary_lines.append("Wygenerowane klucze placeholderow: brak (uruchom Etap 2)")

        ttk.Label(win, text="\n".join(summary_lines), justify="left").pack(anchor="w", padx=10, pady=8)

        body = ttk.Frame(win)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        text = tk.Text(body, wrap="word")
        text.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=yscroll.set)

        text.insert("end", "=== SECTION SCHEMA ===\n")
        text.insert("end", json.dumps(self.section_schema, ensure_ascii=False, indent=2))
        text.insert("end", "\n\n=== STRATEGY JSON ===\n")
        text.insert(
            "end",
            json.dumps(self.page_strategy.raw if self.page_strategy else {}, ensure_ascii=False, indent=2),
        )
        text.insert("end", "\n\n=== GENERATED CONTENT JSON ===\n")
        text.insert(
            "end",
            json.dumps(self.page_content.raw if self.page_content else {}, ensure_ascii=False, indent=2),
        )
        text.insert("end", "\n\n=== MAPPING REPORT ===\n")
        text.insert("end", json.dumps(self.last_mapping_report, ensure_ascii=False, indent=2))
        text.configure(state="disabled")

    def pick_image_for_row(self, row: PlaceholderRow) -> None:
        path = filedialog.askopenfilename(
            title=f"Wybierz plik dla {row.placeholder.pid}",
            filetypes=[("Obrazy", "*.png *.jpg *.jpeg *.webp *.gif *.avif"), ("Wszystkie", "*.*")],
        )
        if path:
            row.image_path.set(path)

    def _client(self, op_id: Optional[int] = None) -> OpenAIClient:
        return OpenAIClient(
            api_key=self.api_key.get(),
            model=self.model.get(),
            read_timeout_seconds=60,
            is_cancelled=(lambda: self._is_operation_cancelled(op_id)) if op_id else None,
            on_timeout_decision=(lambda a, t: self._timeout_decision_dialog(op_id, a, t)) if op_id else None,
            log=self._log,
        )

    def _log(self, message: str) -> None:
        ts = __import__("datetime").datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {message}"
        self._logs.append(entry)
        if len(self._logs) > 3000:
            self._logs = self._logs[-3000:]

        def write() -> None:
            if self._log_text is None:
                return
            try:
                self._log_text.configure(state="normal")
                self._log_text.insert("end", entry + "\n")
                self._log_text.see("end")
                self._log_text.configure(state="disabled")
            except Exception:
                pass

        self.after(0, write)

    def show_live_logs(self) -> None:
        if self._log_window is not None:
            try:
                self._log_window.lift()
                return
            except Exception:
                self._log_window = None
                self._log_text = None

        win = tk.Toplevel(self)
        win.title("Live logi")
        win.geometry("1080x560")
        self._log_window = win

        wrap = ttk.Frame(win)
        wrap.pack(fill="both", expand=True, padx=10, pady=10)
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)

        text = tk.Text(wrap, wrap="none")
        text.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(wrap, orient="vertical", command=text.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll = ttk.Scrollbar(wrap, orient="horizontal", command=text.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self._log_text = text

        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=10, pady=(0, 10))

        def clear_logs() -> None:
            self._logs.clear()
            text.configure(state="normal")
            text.delete("1.0", "end")
            text.configure(state="disabled")

        def copy_logs() -> None:
            payload = "\n".join(self._logs)
            self.clipboard_clear()
            self.clipboard_append(payload)
            self.status.set("Skopiowano logi do schowka.")

        ttk.Button(btns, text="Wyczysc logi", command=clear_logs).pack(side="left")
        ttk.Button(btns, text="Kopiuj logi", command=copy_logs).pack(side="left", padx=8)

        text.configure(state="normal")
        text.insert("end", "\n".join(self._logs) + ("\n" if self._logs else ""))
        text.configure(state="disabled")
        text.see("end")

        def on_close() -> None:
            self._log_window = None
            self._log_text = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)

    def _begin_operation(self, name: str) -> int:
        self._operation_seq += 1
        op_id = self._operation_seq
        self._operations[op_id] = {
            "name": name,
            "cancel_requested": False,
            "done": False,
            "dialog": None,
        }
        self._log(f"[Operation] start id={op_id} name={name}")
        self._schedule_long_wait_dialog(op_id, delay_ms=45000)
        return op_id

    def _is_operation_cancelled(self, op_id: Optional[int]) -> bool:
        if op_id is None:
            return False
        op = self._operations.get(op_id)
        return bool(op and op.get("cancel_requested"))

    def _end_operation(self, op_id: Optional[int]) -> None:
        if op_id is None:
            return
        op = self._operations.get(op_id)
        if not op:
            return
        op["done"] = True
        self._log(f"[Operation] end id={op_id} name={op.get('name')}")
        dialog = op.get("dialog")
        if dialog is not None:
            try:
                dialog.destroy()
            except Exception:
                pass
            op["dialog"] = None

    def _schedule_long_wait_dialog(self, op_id: int, delay_ms: int = 60000) -> None:
        self.after(delay_ms, lambda: self._show_long_wait_dialog(op_id))

    def _show_long_wait_dialog(self, op_id: int) -> None:
        op = self._operations.get(op_id)
        if not op or op.get("done"):
            return
        if op.get("dialog") is not None:
            return

        win = tk.Toplevel(self)
        win.title("Operacja trwa dlugo")
        win.geometry("520x220")
        win.transient(self)
        win.grab_set()
        op["dialog"] = win
        self._log(f"[Operation] long-wait-dialog shown id={op_id} name={op.get('name')}")

        msg = (
            f"Operacja: {op.get('name')}\n\n"
            "To zapytanie trwa dluzej niz zwykle.\n"
            "Co chcesz zrobic?\n\n"
            "- Czekaj: kontynuujemy i pokazemy to okno ponownie po czasie.\n"
            "- Kill: oznacz przerwanie (zatrzymamy przy najblizszej bezpiecznej okazji)."
        )
        ttk.Label(win, text=msg, justify="left").pack(fill="both", expand=True, padx=12, pady=10)

        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=12, pady=(0, 12))

        def on_wait() -> None:
            self._log(f"[Operation] user_wait id={op_id}")
            try:
                win.destroy()
            finally:
                op["dialog"] = None
                if not op.get("done"):
                    self._schedule_long_wait_dialog(op_id, delay_ms=60000)

        def on_kill() -> None:
            op["cancel_requested"] = True
            self._log(f"[Operation] user_kill id={op_id}")
            self.status.set(f"Przerwanie zaznaczone: {op.get('name')}")
            try:
                win.destroy()
            finally:
                op["dialog"] = None

        ttk.Button(btns, text="Czekaj", command=on_wait).pack(side="left")
        ttk.Button(btns, text="Kill", command=on_kill).pack(side="left", padx=8)

    def _timeout_decision_dialog(self, op_id: Optional[int], attempt: int, timeout_sec: int) -> bool:
        if self._is_operation_cancelled(op_id):
            return False

        decision = {"wait": True}
        done = threading.Event()

        def ui() -> None:
            op_name = self._operations.get(op_id, {}).get("name", "Operacja") if op_id else "Operacja"
            win = tk.Toplevel(self)
            win.title("Timeout zapytania API")
            win.geometry("560x240")
            win.transient(self)
            win.grab_set()

            msg = (
                f"{op_name}\n\n"
                f"Brak odpowiedzi przez {timeout_sec} sekund (proba {attempt}).\n"
                "Mozesz czekac dalej albo przerwac.\n\n"
                "Jesli wybierzesz Czekaj, kontynuujemy oczekiwanie i monitorowanie biezacego zadania."
            )
            ttk.Label(win, text=msg, justify="left").pack(fill="both", expand=True, padx=12, pady=10)

            btns = ttk.Frame(win)
            btns.pack(fill="x", padx=12, pady=(0, 12))

            def choose_wait() -> None:
                self._log(f"[Operation] timeout_wait id={op_id} attempt={attempt}")
                decision["wait"] = True
                done.set()
                win.destroy()

            def choose_kill() -> None:
                self._log(f"[Operation] timeout_kill id={op_id} attempt={attempt}")
                decision["wait"] = False
                if op_id in self._operations:
                    self._operations[op_id]["cancel_requested"] = True
                done.set()
                win.destroy()

            ttk.Button(btns, text="Czekaj", command=choose_wait).pack(side="left")
            ttk.Button(btns, text="Kill", command=choose_kill).pack(side="left", padx=8)

        self.after(0, ui)
        done.wait()
        return bool(decision["wait"])

    def generate_one(self, pid: str) -> None:
        ph = next((x for x in self.placeholders if x.pid == pid), None)
        if not ph or ph.kind != "text":
            return

        row = self.rows[pid]
        schema_by_id = self._schema_by_section_id()
        section_entry = schema_by_id.get(ph.section_id, {})
        self.status.set(f"Generowanie tresci dla {pid}...")
        op_id = self._begin_operation(f"Generowanie {pid}")

        def job() -> None:
            try:
                content = self._client(op_id).generate_text(
                    global_prompt=self.global_prompt.get("1.0", "end").strip(),
                    placeholder=ph,
                    optional_prompt=row.get_extra_prompt(),
                    strategy=self.page_strategy,
                    section_schema_entry=section_entry,
                    language_mode=self.language_mode.get(),
                    link_mode=self.link_mode.get(),
                    format_mode=self.format_mode.get(),
                )
                if self._is_operation_cancelled(op_id):
                    self.after(0, lambda: self.status.set(f"Przerwano {pid}."))
                    return
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Blad API", str(exc)))
                self.after(0, lambda: self.status.set("Blad generowania."))
                return
            finally:
                self.after(0, lambda: self._end_operation(op_id))

            content_pp = self._postprocess_generated_text(ph, content)
            self.after(0, lambda: row.set_result(content_pp))
            self.after(0, lambda: self.status.set(f"Wygenerowano {pid}."))

        threading.Thread(target=job, daemon=True).start()

    def generate_all(self) -> None:
        text_placeholders = [p for p in self.placeholders if p.kind == "text"]
        if not text_placeholders:
            messagebox.showwarning("Brak", "Brak placeholderow tekstowych.")
            return

        self.bulk_button.configure(state="disabled")
        if self.page_strategy:
            self.status.set("Etap 2: page-first generowanie calej strony...")
        else:
            self.status.set("Etap 2: brak strategii - page-first na schema fallback...")
        op_id = self._begin_operation("Etap 2: Generowanie contentu")

        def job() -> None:
            try:
                client = self._client(op_id)
                page_result = client.generate_page_content(
                    global_prompt=self.global_prompt.get("1.0", "end").strip(),
                    strategy=self.page_strategy,
                    section_schema=self.section_schema,
                    text_placeholders=text_placeholders,
                    language_mode=self.language_mode.get(),
                    link_mode=self.link_mode.get(),
                    format_mode=self.format_mode.get(),
                )
                if self._is_operation_cancelled(op_id):
                    self.after(0, lambda: self.status.set("Etap 2 przerwany."))
                    return
                self.page_content = page_result

                mapped = page_result.mapped.copy()
                fallback_count = 0
                for ph in text_placeholders:
                    if self._is_operation_cancelled(op_id):
                        self.after(0, lambda: self.status.set("Etap 2 przerwany."))
                        return
                    if ph.pid in mapped and mapped[ph.pid]:
                        continue
                    fallback_count += 1
                    row = self.rows[ph.pid]
                    section_entry = self._schema_by_section_id().get(ph.section_id, {})
                    mapped[ph.pid] = client.generate_text(
                        global_prompt=self.global_prompt.get("1.0", "end").strip(),
                        placeholder=ph,
                        optional_prompt=row.get_extra_prompt(),
                        strategy=self.page_strategy,
                        section_schema_entry=section_entry,
                        language_mode=self.language_mode.get(),
                        link_mode=self.link_mode.get(),
                        format_mode=self.format_mode.get(),
                    )

                for ph in text_placeholders:
                    content = mapped.get(ph.pid, "").strip()
                    if content:
                        content_pp = self._postprocess_generated_text(ph, content)
                        self.after(0, lambda p=ph.pid, c=content_pp: self.rows[p].set_result(c))

                self.last_mapping_report = {
                    "placeholders_requested": len(text_placeholders),
                    "generated_keys": len(page_result.mapped),
                    "missing_from_page_json": page_result.missing_ids,
                    "fallback_count": fallback_count,
                    "mapped_success": len([p for p in text_placeholders if mapped.get(p.pid)]),
                }
                self.after(
                    0,
                    lambda: self.status.set(
                        f"Etap 2 gotowy. JSON keys={len(page_result.mapped)}, fallback={fallback_count}."
                    ),
                )
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Blad API", str(exc)))
                self.after(0, lambda: self.status.set("Generowanie przerwane."))
            finally:
                self.after(0, lambda: self.bulk_button.configure(state="normal"))
                self.after(0, lambda: self._end_operation(op_id))

        threading.Thread(target=job, daemon=True).start()

    def generate_one_image_metadata(self, pid: str) -> None:
        ph = next((x for x in self.placeholders if x.pid == pid), None)
        if not ph or ph.kind != "image":
            return

        row = self.rows[pid]
        self.status.set(f"Generowanie metadanych dla {pid}...")
        op_id = self._begin_operation(f"Metadane {pid}")

        def job() -> None:
            try:
                meta = self._client(op_id).generate_image_metadata(
                    global_prompt=self.global_prompt.get("1.0", "end").strip(),
                    placeholder_id=pid,
                    image_reference=row.get_image_value() or ph.original,
                    optional_prompt=row.get_image_prompt(),
                )
                if self._is_operation_cancelled(op_id):
                    self.after(0, lambda: self.status.set(f"Przerwano metadane {pid}."))
                    return
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Blad API", str(exc)))
                self.after(0, lambda: self.status.set("Blad metadanych."))
                return
            finally:
                self.after(0, lambda: self._end_operation(op_id))

            self.after(
                0,
                lambda: row.set_image_metadata(
                    meta.get("filename", ""), meta.get("alt", ""), meta.get("description", "")
                ),
            )
            self.after(0, lambda: self.status.set(f"Wygenerowano metadane {pid}."))

        threading.Thread(target=job, daemon=True).start()

    def generate_all_image_metadata(self) -> None:
        images = [p for p in self.placeholders if p.kind == "image"]
        if not images:
            messagebox.showwarning("Brak", "Brak placeholderow obrazow.")
            return

        self.bulk_image_meta_button.configure(state="disabled")
        self.status.set("Generowanie metadanych obrazow...")
        op_id = self._begin_operation("Etap 4: Metadane obrazow")

        def job() -> None:
            try:
                client = self._client(op_id)
                for ph in images:
                    if self._is_operation_cancelled(op_id):
                        self.after(0, lambda: self.status.set("Metadane przerwane."))
                        return
                    row = self.rows[ph.pid]
                    meta = client.generate_image_metadata(
                        global_prompt=self.global_prompt.get("1.0", "end").strip(),
                        placeholder_id=ph.pid,
                        image_reference=row.get_image_value() or ph.original,
                        optional_prompt=row.get_image_prompt(),
                    )
                    self.after(
                        0,
                        lambda p=ph.pid, m=meta: self.rows[p].set_image_metadata(
                            m.get("filename", ""),
                            m.get("alt", ""),
                            m.get("description", ""),
                        ),
                    )
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Blad API", str(exc)))
                self.after(0, lambda: self.status.set("Generowanie metadanych przerwane."))
            finally:
                self.after(0, lambda: self.bulk_image_meta_button.configure(state="normal"))
                self.after(0, lambda: self.status.set("Metadane obrazow gotowe."))
                self.after(0, lambda: self._end_operation(op_id))

        threading.Thread(target=job, daemon=True).start()

    def _build_output_from_spans(self) -> Tuple[str, Dict[str, str]]:
        if not self.template_raw or not self.placeholders:
            return "", {}

        replacements: Dict[str, str] = {}
        chunks: List[str] = []
        last = 0

        for ph in sorted(self.placeholders, key=lambda x: x.start):
            row = self.rows[ph.pid]
            value = row.get_result() if ph.kind == "text" else row.get_image_value()
            value = value or ph.original

            replacements[ph.pid] = value
            chunks.append(self.template_raw[last : ph.start])
            chunks.append(value)
            last = ph.end

        chunks.append(self.template_raw[last:])
        return "".join(chunks), replacements

    def build_output(self) -> None:
        if not self.template_raw or not self.placeholders:
            messagebox.showwarning("Brak", "Najpierw wczytaj szablon.")
            return

        output, replacements = self._build_output_from_spans()
        out_path = Path(self.output_path.get().strip())
        out_path.write_text(output, encoding="utf-8")

        validation = self.validate_output_text(output, replacements)
        report_path = out_path.with_name(f"{out_path.stem}_validation_report.txt")
        report_path.write_text(validation["report_text"], encoding="utf-8")
        generation_report_path = out_path.with_name(f"{out_path.stem}_generation_report.txt")
        generation_report_path.write_text(self._build_generation_report(replacements), encoding="utf-8")

        self.status.set(f"Zapisano: {out_path}")
        messagebox.showinfo(
            "Sukces",
            f"Wygenerowano plik:\n{out_path}\n\n"
            f"Walidacja: {validation['critical']} krytycznych, {validation['warnings']} ostrzezen\n"
            f"Raport walidacji: {report_path}\n"
            f"Raport generacji: {generation_report_path}",
        )

    def _build_generation_report(self, replacements: Dict[str, str]) -> str:
        lines = [
            "AVADA Generation Report",
            "======================",
            "",
            "Runtime modes:",
            f"- generation_mode: {self.generation_mode.get()}",
            f"- language_mode: {self.language_mode.get()}",
            f"- link_mode: {self.link_mode.get()}",
            f"- format_mode: {self.format_mode.get()}",
            "",
            "Detected AVADA block types:",
        ]
        for key, val in self.last_scan_report.items():
            lines.append(f"- {key}: {val}")

        lines.append("")
        lines.append("Inferred logical sections:")
        lines.append(json.dumps(self.section_schema, ensure_ascii=False, indent=2))

        lines.append("")
        lines.append("Generated structured keys:")
        if self.page_content:
            keys = sorted(self.page_content.mapped.keys())
            lines.append(f"- count: {len(keys)}")
            lines.append("- keys: " + ", ".join(keys))
        else:
            lines.append("- brak (nie uruchomiono Etapu 2 page-first)")

        lines.append("")
        lines.append("Mapping success/failure:")
        mapped_ok = 0
        mapped_missing: List[str] = []
        for ph in [p for p in self.placeholders if p.kind == "text"]:
            if replacements.get(ph.pid):
                mapped_ok += 1
            else:
                mapped_missing.append(ph.pid)
        lines.append(f"- success: {mapped_ok}")
        lines.append(f"- missing: {len(mapped_missing)}")
        if mapped_missing:
            lines.append("- missing_ids: " + ", ".join(mapped_missing))

        text_changed = 0
        text_unchanged = 0
        image_changed = 0
        for ph in self.placeholders:
            rep = replacements.get(ph.pid, ph.original)
            if ph.kind == "text":
                if self._normalize_text(rep) != self._normalize_text(ph.original):
                    text_changed += 1
                else:
                    text_unchanged += 1
            else:
                if self._normalize_text(rep) != self._normalize_text(ph.original):
                    image_changed += 1
        lines.append(f"- changed_text_placeholders: {text_changed}")
        lines.append(f"- unchanged_text_placeholders: {text_unchanged}")
        lines.append(f"- changed_image_placeholders: {image_changed}")

        if self.last_mapping_report:
            lines.append("- runtime_mapping_report:")
            lines.append(json.dumps(self.last_mapping_report, ensure_ascii=False, indent=2))
        return "\n".join(lines)

    def save_image_metadata(self) -> None:
        metadata: List[Dict[str, str]] = []
        for ph in self.placeholders:
            if ph.kind != "image":
                continue
            row = self.rows[ph.pid]
            meta = row.get_image_metadata()
            metadata.append(
                {
                    "placeholder": ph.pid,
                    "block_type": ph.block_type,
                    "field": ph.field,
                    "image_source": row.get_image_value() or ph.original,
                    "filename": meta.get("filename", ""),
                    "alt": meta.get("alt", ""),
                    "description": meta.get("description", ""),
                }
            )

        out_path = Path(self.image_metadata_path.get().strip())
        out_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        self.status.set(f"Zapisano metadane: {out_path}")
        messagebox.showinfo("Sukces", f"Zapisano metadane obrazow:\n{out_path}")

    def compare_outputs(self) -> None:
        template_path = Path(self.template_path.get().strip())
        compare_file = Path(self.compare_path.get().strip())

        if not template_path.exists() or not compare_file.exists():
            messagebox.showerror("Blad", "Sprawdz sciezki plikow do porownania.")
            return

        src_raw = self._read_text(template_path)
        out_raw = self._read_text(compare_file)

        src = [p for p in self.parser.parse(src_raw).placeholders if p.kind == "text"]
        out = [p for p in self.parser.parse(out_raw).placeholders if p.kind == "text"]

        max_len = max(len(src), len(out))
        rows: List[Tuple[str, str, str, str]] = []
        changed = 0
        unchanged = 0
        suspicious = 0

        for i in range(max_len):
            src_p = src[i] if i < len(src) else None
            out_p = out[i] if i < len(out) else None
            pid = src_p.pid if src_p else (out_p.pid if out_p else f"TEXT_{i+1:03d}")
            src_text = src_p.original if src_p else ""
            out_text = out_p.original if out_p else ""

            if not src_text and out_text:
                status = "DODANY"
                color = "green"
                changed += 1
            elif src_text and not out_text:
                status = "USUNIETY"
                color = "red"
                unchanged += 1
            elif self._normalize_text(src_text) == self._normalize_text(out_text):
                status = "NIEZMIENIONY / POMINIETY"
                color = "red"
                unchanged += 1
            else:
                similarity = SequenceMatcher(None, self._normalize_text(src_text), self._normalize_text(out_text)).ratio()
                if similarity >= 0.75:
                    status = f"BARDZO PODOBNY ({similarity:.0%})"
                    color = "red"
                    suspicious += 1
                else:
                    status = f"ZMIENIONY ({similarity:.0%} podobienstwa)"
                    color = "green"
                    changed += 1

            block = out_p.block_type if out_p else (src_p.block_type if src_p else "unknown")
            excerpt = out_text[:160].replace("\n", " ")
            rows.append((f"{pid} [{block}]", status, color, excerpt))

        self._show_compare_report(rows, changed, unchanged, suspicious, len(src), len(out), compare_file)

    def _show_compare_report(
        self,
        rows: List[Tuple[str, str, str, str]],
        changed: int,
        unchanged: int,
        suspicious: int,
        src_total: int,
        out_total: int,
        compare_file: Path,
    ) -> None:
        win = tk.Toplevel(self)
        win.title("Raport porownania")
        win.geometry("1050x700")

        summary = (
            f"Plik porownywany: {compare_file}\n"
            f"Bloki tekstowe w szablonie: {src_total}, w output: {out_total}\n"
            f"Zmienione: {changed} | Niezmienione/pominiete: {unchanged} | Bardzo podobne: {suspicious}\n"
            "Kolory: zielony = zmienione, czerwony = niezmienione/pominiete/podejrzanie podobne."
        )

        ttk.Label(win, text=summary, justify="left").pack(anchor="w", padx=10, pady=10)

        text = tk.Text(win, wrap="word")
        text.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        text.tag_configure("green", foreground="#0b7a0b")
        text.tag_configure("red", foreground="#b10000")
        text.tag_configure("header", font=("Segoe UI", 10, "bold"))

        for pid, status, color, excerpt in rows:
            text.insert("end", f"{pid}: ", "header")
            text.insert("end", f"{status}\n", color)
            text.insert("end", f"  -> {excerpt or '(brak tresci)'}\n\n")

        text.configure(state="disabled")

    def _detect_lang(self, text: str) -> str:
        lowered = text.lower()
        pl_hits = len(re.findall(r"\b(i|oraz|jest|lazienka|lazienki|w|na|dla)\b", lowered))
        en_hits = len(re.findall(r"\b(and|the|is|are|with|for|garden|room)\b", lowered))
        if pl_hits > 0 and en_hits > 0:
            return "mixed"
        if pl_hits > 0:
            return "pl"
        if en_hits > 0:
            return "en"
        return "unknown"

    def _source_topic_terms(self, limit: int = 20) -> List[str]:
        if not self.template_raw:
            return []
        src_text_blocks = [p.original for p in self.parser.parse(self.template_raw).placeholders if p.kind == "text"]
        bag = " ".join(src_text_blocks).lower()
        tokens = [t for t in re.findall(r"[a-z0-9-]{4,}", bag) if t not in PL_STOPWORDS and t not in EN_STOPWORDS]
        freq: Dict[str, int] = {}
        for t in tokens:
            freq[t] = freq.get(t, 0) + 1
        ranked = sorted(freq.items(), key=lambda x: x[1], reverse=True)
        return [k for k, _ in ranked[:limit]]

    def _target_terms_from_prompt(self) -> List[str]:
        prompt = self.global_prompt.get("1.0", "end").lower()
        tokens = [t for t in re.findall(r"[a-z0-9-]{4,}", prompt) if t not in PL_STOPWORDS and t not in EN_STOPWORDS]
        return sorted(set(tokens))

    def validate_output_text(self, output: str, replacements: Optional[Dict[str, str]] = None) -> Dict[str, object]:
        issues: List[str] = []
        warnings: List[str] = []

        marker_hits = re.findall(r"\b(?:__)?(?:TEXT|IMG|CBTITLE|CBBODY)_\d{3}(?:__)?\b", output)
        if marker_hits:
            issues.append(f"Wykryto niepodmienione markery: {', '.join(sorted(set(marker_hits))[:20])}")

        topic = self._detect_target_topic()
        lower_output = output.lower()
        found_old_curated = [kw for kw in GARDEN_TERMS if kw in lower_output]
        if found_old_curated:
            warnings.append("Wykryto stare frazy domenowe: " + ", ".join(found_old_curated[:8]))

        source_terms = self._source_topic_terms()
        target_terms = self._target_terms_from_prompt()
        source_terms_not_target = [t for t in source_terms if t not in target_terms]
        source_hits = [t for t in source_terms_not_target if re.search(rf"\b{re.escape(t)}\b", lower_output)]
        if len(source_hits) >= 8:
            issues.append(
                "Silna kontaminacja semantyczna ze zrodla (source-topic terms): "
                + ", ".join(source_hits[:12])
            )
        elif len(source_hits) >= 4:
            warnings.append("Wykryto dziedziczenie konceptow zrodlowych: " + ", ".join(source_hits[:10]))

        if replacements is not None and not self.page_strategy:
            warnings.append("Brak strategii Etap 1 - content byl generowany fallbackiem blok-po-bloku.")
        elif replacements is not None and self.section_schema:
            missing_section_plan = []
            for x in self.section_schema:
                sid = int(x["section_id"])
                if sid not in self.page_strategy.sections:
                    missing_section_plan.append(sid)
            if missing_section_plan:
                warnings.append(
                    "Strategia nie pokryla wszystkich sekcji (fallback aktywny): "
                    + ", ".join(str(x) for x in missing_section_plan)
                )

        output_text_blocks = [p for p in self.parser.parse(output).placeholders if p.kind == "text"]
        langs = [self._detect_lang(p.original) for p in output_text_blocks if p.original.strip()]
        en_blocks = sum(1 for x in langs if x == "en")
        mixed_blocks = sum(1 for x in langs if x == "mixed")
        required_lang = self._required_language()
        if required_lang == "en":
            bad = [p.pid for p in output_text_blocks if self._detect_lang(p.original) in ("pl", "mixed")]
            if bad:
                issues.append("Tryb english-only naruszony w blokach: " + ", ".join(bad[:20]))
        elif required_lang == "pl":
            bad = [p.pid for p in output_text_blocks if self._detect_lang(p.original) in ("en", "mixed")]
            if bad:
                issues.append("Tryb polish-only naruszony w blokach: " + ", ".join(bad[:20]))
        else:
            if en_blocks > 0 and mixed_blocks > max(1, len(langs) // 4):
                warnings.append(
                    f"Wykryto potencjalnie mieszany jezyk: EN={en_blocks}, mixed={mixed_blocks}, wszystkie={len(langs)}"
                )

        if self.template_raw:
            src_text_blocks = [p for p in self.parser.parse(self.template_raw).placeholders if p.kind == "text"]
            unchanged_or_near = []
            for idx in range(min(len(src_text_blocks), len(output_text_blocks))):
                a = self._normalize_text(src_text_blocks[idx].original)
                b = self._normalize_text(output_text_blocks[idx].original)
                sim = SequenceMatcher(None, a, b).ratio()
                if sim >= 0.90:
                    unchanged_or_near.append((output_text_blocks[idx].pid, sim))
            if len(unchanged_or_near) >= 3:
                warnings.append(
                    "Sekcje niezmienione/prawie niezmienione ze zrodla: "
                    + ", ".join(f"{pid}({sim:.0%})" for pid, sim in unchanged_or_near[:10])
                )

        if replacements:
            src_by_pid = {p.pid: p for p in self.placeholders}
            unchanged_cb = []
            for pid, value in replacements.items():
                src = src_by_pid.get(pid)
                if not src or src.block_type != "fusion_content_box":
                    continue
                if self._normalize_text(value) == self._normalize_text(src.original):
                    unchanged_cb.append(f"{pid} ({src.field})")
            if unchanged_cb:
                warnings.append("Niezmienione tresci fusion_content_box: " + ", ".join(unchanged_cb[:20]))
        elif self.template_raw:
            src_boxes = [
                p
                for p in self.parser.parse(self.template_raw).placeholders
                if p.kind == "text" and p.block_type == "fusion_content_box"
            ]
            out_boxes = [
                p
                for p in output_text_blocks
                if p.block_type == "fusion_content_box"
            ]
            unchanged_cb = []
            for idx in range(min(len(src_boxes), len(out_boxes))):
                if self._normalize_text(src_boxes[idx].original) == self._normalize_text(out_boxes[idx].original):
                    unchanged_cb.append(f"{out_boxes[idx].pid} ({out_boxes[idx].field})")
            if unchanged_cb:
                warnings.append(
                    "Niezmienione tresci fusion_content_box (porownanie ze zrodlem): "
                    + ", ".join(unchanged_cb[:20])
                )

        if replacements:
            text_placeholders = [p for p in self.placeholders if p.kind == "text"]
            by_section: Dict[int, List[Tuple[Placeholder, str]]] = {}
            for ph in text_placeholders:
                value = replacements.get(ph.pid, ph.original)
                by_section.setdefault(ph.section_id, []).append((ph, value))

            for section_id, pairs in by_section.items():
                pairs.sort(key=lambda x: x[0].start)
                for i in range(len(pairs) - 1):
                    heading = pairs[i][1]
                    body = pairs[i + 1][1]
                    if self._count_words(heading) <= 14 and self._count_words(body) >= 25:
                        h_kw = set(self._tokenize_keywords(heading))
                        b_kw = set(self._tokenize_keywords(body))
                        if h_kw:
                            overlap = len(h_kw & b_kw) / max(1, len(h_kw))
                            if overlap < 0.08:
                                warnings.append(
                                    f"Sekcja {section_id}: mozliwy mismatch naglowka i tresci "
                                    f"({pairs[i][0].pid} -> {pairs[i + 1][0].pid})."
                                )

                cb_titles = [x for x in pairs if x[0].block_type == "fusion_content_box" and x[0].field == "title"]
                cb_bodies = [x for x in pairs if x[0].block_type == "fusion_content_box" and x[0].field == "body"]
                for idx in range(min(len(cb_titles), len(cb_bodies))):
                    title_kw = set(self._tokenize_keywords(cb_titles[idx][1]))
                    body_kw = set(self._tokenize_keywords(cb_bodies[idx][1]))
                    if title_kw:
                        overlap = len(title_kw & body_kw) / max(1, len(title_kw))
                        if overlap < 0.08:
                            warnings.append(
                                f"Sekcja {section_id}: mismatch content_box title/body "
                                f"({cb_titles[idx][0].pid} -> {cb_bodies[idx][0].pid})."
                            )

        # Workflow-aware image validation: unchanged images are expected in text-only mode.
        if replacements is not None:
            image_placeholders = [p for p in self.placeholders if p.kind == "image"]
            unchanged_images = []
            for ph in image_placeholders:
                rep = replacements.get(ph.pid, ph.original)
                if self._normalize_text(rep) == self._normalize_text(ph.original):
                    unchanged_images.append(ph.pid)
            if self.generation_mode.get() == "text+image":
                if unchanged_images:
                    warnings.append(
                        "Tryb text+image, ale niezmienione obrazy: "
                        + ", ".join(unchanged_images[:20])
                    )

        changed = 0
        if replacements:
            for ph in self.placeholders:
                value = replacements.get(ph.pid, ph.original)
                if self._normalize_text(value) != self._normalize_text(ph.original):
                    changed += 1

        report_lines = [
            "AVADA Validation Report",
            "======================",
            "",
            "Elementy wykryte podczas skanowania:",
        ]
        for key, val in self.last_scan_report.items():
            report_lines.append(f"- {key}: {val}")
        report_lines.append(f"- Lacznie placeholderow: {len(self.placeholders)}")
        report_lines.append(f"- Zmienione placeholdery: {changed}")
        report_lines.append("")
        report_lines.append("Krytyczne problemy:")
        report_lines.extend([f"- {x}" for x in issues] if issues else ["- Brak"])
        report_lines.append("")
        report_lines.append("Ostrzezenia:")
        report_lines.extend([f"- {x}" for x in warnings] if warnings else ["- Brak"])

        return {
            "critical": len(issues),
            "warnings": len(warnings),
            "issues": issues,
            "warning_items": warnings,
            "report_text": "\n".join(report_lines),
        }

    def validate_existing_output(self) -> None:
        out_path = Path(self.output_path.get().strip())
        if not out_path.exists():
            messagebox.showerror("Blad", f"Nie znaleziono outputu: {out_path}")
            return

        raw = self._read_text(out_path)
        validation = self.validate_output_text(raw, replacements=None)
        report_path = out_path.with_name(f"{out_path.stem}_validation_report.txt")
        report_path.write_text(validation["report_text"], encoding="utf-8")

        messagebox.showinfo(
            "Walidacja outputu",
            f"Krytyczne: {validation['critical']}\nOstrzezenia: {validation['warnings']}\n\nRaport: {report_path}",
        )


if __name__ == "__main__":
    app = App()
    app.mainloop()

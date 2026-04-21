import json
import re
import time
from typing import Any, Callable, Dict, List, Optional

import requests

from .models import PageContentResult, Placeholder, StrategyResult


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
                    if not self.on_timeout_decision(attempt, self.read_timeout_seconds):
                        raise RuntimeError("Przerwano po timeout na zadanie uzytkownika.")
                continue

            if response.status_code >= 400:
                self._log(f"http_error body_preview={response.text[:350]}")
                raise RuntimeError(f"Blad API {response.status_code}: {response.text[:500]}")

            data = response.json()
            self._log(f"response_id={data.get('id', '')} status={data.get('status', '')}")
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
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    timeout=(20, self.read_timeout_seconds),
                )
            except requests.Timeout:
                self._log(f"poll_timeout response_id={response_id} poll={poll_attempt}")
                if self.on_timeout_decision:
                    if not self.on_timeout_decision(poll_attempt, self.read_timeout_seconds):
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
                self._log(f"terminal_non_completed_status={status} error={data.get('error')}")
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

    def generate_page_strategy(self, global_prompt: str, section_schema: List[Dict[str, object]]) -> StrategyResult:
        prompt = (
            "Jestes strategiem SEO i content architectem. Przygotuj strategie strony i plan sekcji pod podany szablon AVADA. "
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
            "Kazda sekcja musi miec section_id zgodne ze schema. Nie pomijaj sekcji.\n\n"
            f"Kontekst strony:\n{global_prompt.strip()}\n\n"
            f"Schema sekcji:\n{json.dumps(section_schema, ensure_ascii=False)}"
        )
        raw = self._request(prompt, temperature=0.5)
        data = self._extract_json_object(raw)
        section_map: Dict[int, Dict[str, object]] = {}
        raw_sections = data.get("sections", [])
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
            "Wygeneruj tresc SEO dla CALEJ strony w JEDNYM przebiegu. Nie generuj blok po bloku. "
            "Zachowaj spojna semantyke calej strony.\n\n"
            "Zwroc WYLACZNIE JSON w formacie:\n"
            "{\n"
            '  "page_title": "string",\n'
            '  "page_summary": "string",\n'
            '  "sections": [\n'
            "    {\n"
            '      "section_id": 1,\n'
            '      "section_goal": "string",\n'
            '      "placeholders": [\n'
            '        {"pid":"TEXT_001","content":"..."}\n'
            "      ]\n"
            "    }\n"
            "  ],\n"
            '  "placeholders": {"TEXT_001":"..."}\n'
            "}\n\n"
            "Reguly krytyczne:\n"
            "- Uwzglednij WSZYSTKIE placeholdery z listy.\n"
            "- Nie zwracaj markerow typu TEXT_001 jako tresci.\n"
            "- Nie mieszaj starego tematu z nowym.\n"
            "- Dlugosc tresci trzymaj blisko target_words.\n"
            "- Trzymaj cel i role sekcji zgodnie z section_kind i role_hint.\n"
            "- Jesli source_excerpt placeholdera zawiera <h1>/<h2>/<h3>, zwracaj tresc z odpowiednim tagiem heading HTML.\n"
            "- Dla heading-like blokow: pierwsza linia = naglowek, pusta linia, potem body.\n"
            "- Dla content_box: title i body musza byc semantycznie spojne.\n"
            "- Dla fusion_li_item (checklist): przepisz kazdy punkt pod nowy temat strony, nie kopiuj starej semantyki.\n"
            "- Linkowanie wewnetrzne osadzaj naturalnie w akapitach i zdaniach.\n"
            "- Nie tworz sekcji skladajacych sie z samych linkow jeden pod drugim.\n"
            "- Przykład kierunku: kitchens -> Kitchen Renovations, bathrooms -> Bathroom Installations, structural changes -> Structural Work, finishing -> Interior Finishing.\n"
            f"- Tryb jezyka: {language_mode}\n"
            f"- Tryb linkow: {link_mode} (strict => <a href=\"URL\">Anchor</a> dla internal links)\n"
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
            "Napisz tekst SEO do sekcji strony. Dbaj o spojna tematyke i NIE mieszaj tematow. "
            "Nie zostawiaj markerow typu TEXT_001. "
            f"Docelowa dlugosc: okolo {placeholder.suggested_words} slow (+/- 15%).\n\n"
            f"Kontekst globalny:\n{global_prompt.strip()}\n\n"
            f"Strategia strony (JSON):\n{json.dumps(strategy_payload, ensure_ascii=False)}\n\n"
            f"Plan sekcji (JSON):\n{json.dumps(section_plan, ensure_ascii=False)}\n\n"
            f"Schema sekcji (JSON):\n{json.dumps(schema_entry, ensure_ascii=False)}\n\n"
            f"Typ bloku: {placeholder.block_type}, pole: {placeholder.field}, id: {placeholder.pid}\n"
            f"Tresc zrodlowa:\n{placeholder.original.strip()}\n\n"
            f"Tryb jezyka: {language_mode}\n"
            f"Tryb linkow: {link_mode}\n"
            f"Tryb formatowania: {format_mode}\n\n"
            "Jesli tresc zrodlowa zawiera <h1>/<h2>/<h3>, zachowaj heading HTML w wyniku.\n\n"
            "Jesli typ bloku to fusion_li_item, zwroc jeden punkt checklisty pod nowy temat (bez dziedziczenia starego tematu).\n"
            "Linkowanie wewnetrzne osadzaj naturalnie w akapicie. Nie tworz bloku samych linkow.\n\n"
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
            "Tworzysz metadane SEO obrazka. Zwroc WYLACZNIE JSON: "
            '{"filename":"...","alt":"...","description":"..."}. '
            "filename: ascii, male litery, myslniki, bez rozszerzenia. "
            "alt: max okolo 140 znakow. description: 1-2 zdania.\n\n"
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

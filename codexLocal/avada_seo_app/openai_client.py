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
        return self._request_with_input(prompt, temperature=temperature)

    def _request_with_input(self, input_payload: object, temperature: float = 0.7) -> str:
        if not self.api_key:
            raise ValueError("Brak klucza OPENAI_API_KEY.")

        data = self._create_background_response(input_payload, temperature)
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

    def _create_background_response(self, input_payload: object, temperature: float) -> Dict[str, Any]:
        attempt = 0
        while True:
            if self.is_cancelled and self.is_cancelled():
                raise RuntimeError("Operacja przerwana przez uzytkownika.")
            attempt += 1
            payload_size = len(str(input_payload)) if isinstance(input_payload, str) else len(json.dumps(input_payload, ensure_ascii=False))
            self._log(
                f"attempt={attempt} model={self.model} prompt_chars={payload_size} timeout_read={self.read_timeout_seconds}s background=true"
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
                        "input": input_payload,
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

    def polishing_validate_page(
        self,
        global_prompt: str,
        section_schema: List[Dict[str, object]],
        units: List[Dict[str, object]],
        mode: str = "BALANCED",
    ) -> Dict[str, object]:
        prompt = (
            "You are validating and minimally polishing already generated local SEO page content for a builder website using Avada placeholders.\n"
            "Do NOT rewrite the page from scratch.\n"
            "Preserve meaning, section purpose, SEO intent, tone and structure.\n"
            "Only improve detected weak points with minimal changes.\n\n"
            "Polishing mode: "
            + mode
            + "\n"
            "STRICT = very minimal edits only.\n"
            "BALANCED = safe improvements while preserving tone and SEO intent.\n"
            "AGGRESSIVE = deeper local section rewrite allowed, never whole-page rewrite.\n\n"
            "Validation rules:\n"
            "- topic purity (no source-topic leakage)\n"
            "- keyword usage (no stuffing, preserve intent)\n"
            "- internal linking natural in paragraphs (no link-dump sections)\n"
            "- readability\n"
            "- conversion/CTA quality\n"
            "- preserve section structure and purpose\n\n"
            "Return ONLY valid JSON in this schema:\n"
            "{\n"
            '  "page_scores": {"seo": 1-10, "readability": 1-10, "conversion": 1-10, "topic_cleanliness": 1-10, "internal_linking": 1-10, "overall": 1-10},\n'
            '  "issues": [{"id":"...", "severity":"low|medium|high", "scope":"page|unit", "unit_id":"...", "category":"seo|readability|conversion|topic_cleanliness|internal_linking", "message":"..."}],\n'
            '  "units": [\n'
            "    {\n"
            '      "unit_id":"...",\n'
            '      "unit_type":"fusion_text|fusion_content_box|fusion_checklist|... ",\n'
            '      "scores":{"seo":1-10,"readability":1-10,"conversion":1-10,"topic_cleanliness":1-10,"internal_linking":1-10,"overall":1-10},\n'
            '      "issues":[{"id":"...","severity":"low|medium|high","category":"seo|readability|conversion|topic_cleanliness|internal_linking","message":"..."}],\n'
            '      "suggested_fix":"...",\n'
            '      "polished_text":"..."\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            f"Global prompt/context:\n{global_prompt.strip()}\n\n"
            f"Section schema:\n{json.dumps(section_schema, ensure_ascii=False)}\n\n"
            f"Content units:\n{json.dumps(units, ensure_ascii=False)}"
        )
        raw = self._request(prompt, temperature=0.2)
        return self._extract_json_object(raw)

    def generate_image_plan(
        self,
        global_prompt: str,
        section_schema: List[Dict[str, object]],
        image_placeholder_ids: List[str],
        images_manifest: List[Dict[str, object]],
        contact_sheet_data_url: str,
    ) -> Dict[str, object]:
        prompt = (
            "You are selecting project images for an AVADA page.\n"
            "Goal: choose best images, assign them to image placeholders, and generate SEO metadata.\n"
            "Keep decisions practical and relevant to page topic.\n"
            "Return ONLY valid JSON.\n\n"
            "Schema:\n"
            "{\n"
            '  "selected": [\n'
            "    {\n"
            '      "image_key": "original filename from manifest",\n'
            '      "placeholder_id": "IMG_001",\n'
            '      "score": 0-10,\n'
            '      "short_reason": "short explanation",\n'
            '      "seo_filename": "seo-friendly-name",\n'
            '      "alt": "...",\n'
            '      "caption": "...",\n'
            '      "description": "...",\n'
            '      "reason": "..."\n'
            "    }\n"
            "  ],\n"
            '  "not_selected": [{"image_key":"...", "reason":"..."}]\n'
            "}\n\n"
            "Rules:\n"
            "- Assign at most one image per placeholder.\n"
            "- Use only placeholders from provided list.\n"
            "- Use only image_key values from manifest.\n"
            "- Keep metadata SEO-safe and natural.\n"
            "- filename must be lowercase, ascii, hyphen-separated, no extension.\n\n"
            f"Page context:\n{global_prompt.strip()}\n\n"
            f"Image placeholders:\n{json.dumps(image_placeholder_ids, ensure_ascii=False)}\n\n"
            f"Section schema:\n{json.dumps(section_schema, ensure_ascii=False)}\n\n"
            f"Image manifest:\n{json.dumps(images_manifest, ensure_ascii=False)}\n"
        )
        input_payload: List[Dict[str, object]] = [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}]
        if contact_sheet_data_url:
            input_payload[0]["content"].append({"type": "input_image", "image_url": contact_sheet_data_url})
        raw = self._request_with_input(input_payload, temperature=0.25)
        return self._extract_json_object(raw)

    def improve_one_image_metadata(
        self,
        global_prompt: str,
        image_key: str,
        current_metadata: Dict[str, str],
        contact_image_data_url: str,
        assigned_placeholder_id: str = "",
    ) -> Dict[str, str]:
        prompt = (
            "Improve SEO metadata for ONE image.\n"
            "Do minimal improvement, keep meaning.\n"
            "Return ONLY JSON: {\"seo_filename\":\"...\",\"alt\":\"...\",\"caption\":\"...\",\"description\":\"...\"}\n"
            "seo_filename: lowercase ascii hyphens, no extension.\n\n"
            f"Page context:\n{global_prompt.strip()}\n\n"
            f"Image key: {image_key}\n"
            f"Assigned placeholder: {assigned_placeholder_id or '(none)'}\n"
            f"Current metadata: {json.dumps(current_metadata, ensure_ascii=False)}\n"
        )
        input_payload: List[Dict[str, object]] = [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}]
        if contact_image_data_url:
            input_payload[0]["content"].append({"type": "input_image", "image_url": contact_image_data_url})
        raw = self._request_with_input(input_payload, temperature=0.2)
        data = self._extract_json_object(raw)
        return {
            "seo_filename": str(data.get("seo_filename", "")).strip(),
            "alt": str(data.get("alt", "")).strip(),
            "caption": str(data.get("caption", "")).strip(),
            "description": str(data.get("description", "")).strip(),
        }

    def generate_seo_title_slug(
        self,
        global_prompt: str,
        strategy: Optional[StrategyResult],
        section_schema: List[Dict[str, object]],
        sample_output_excerpt: str,
    ) -> Dict[str, str]:
        prompt = (
            "Generate SEO-ready metadata for a local service page.\n"
            "Return ONLY JSON: {\"title\":\"...\",\"slug\":\"...\",\"focus_keyphrase\":\"...\",\"meta_description\":\"...\"}\n"
            "Rules:\n"
            "- title: clear, local-intent, natural, 45-70 chars preferred\n"
            "- slug: lowercase ascii, hyphen-separated, no stopwords if possible, no trailing slash\n"
            "- focus_keyphrase: 2-6 words, strong local/service intent\n"
            "- meta_description: 120-155 chars preferred, persuasive, natural, no keyword stuffing\n"
            "- keep service/location intent strong\n\n"
            f"Global prompt:\n{global_prompt.strip()}\n\n"
            f"Strategy JSON:\n{json.dumps(strategy.raw if strategy else {}, ensure_ascii=False)}\n\n"
            f"Section schema:\n{json.dumps(section_schema, ensure_ascii=False)}\n\n"
            f"Generated page excerpt:\n{sample_output_excerpt[:2000]}"
        )
        raw = self._request(prompt, temperature=0.25)
        data = self._extract_json_object(raw)
        return {
            "title": str(data.get("title", "")).strip(),
            "slug": str(data.get("slug", "")).strip(),
            "focus_keyphrase": str(data.get("focus_keyphrase", "")).strip(),
            "meta_description": str(data.get("meta_description", "")).strip(),
        }

    def polishing_fix_unit(
        self,
        global_prompt: str,
        unit: Dict[str, object],
        mode: str = "BALANCED",
        issue_category: str = "",
        issue_message: str = "",
    ) -> Dict[str, object]:
        prompt = (
            "You are performing targeted polishing for ONE content unit from an already generated local SEO page.\n"
            "Do NOT rewrite the whole page and do NOT change topic.\n"
            "Preserve meaning, service relevance, keywords intent and tone.\n"
            "Apply minimal correction for the target issue/scope only.\n\n"
            f"Mode: {mode}\n"
            f"Target issue category: {issue_category or '(auto)'}\n"
            f"Target issue message: {issue_message or '(auto)'}\n\n"
            "Return ONLY valid JSON:\n"
            "{\n"
            '  "unit_id":"...",\n'
            '  "unit_type":"...",\n'
            '  "scores":{"seo":1-10,"readability":1-10,"conversion":1-10,"topic_cleanliness":1-10,"internal_linking":1-10,"overall":1-10},\n'
            '  "issues":[{"id":"...","severity":"low|medium|high","category":"seo|readability|conversion|topic_cleanliness|internal_linking","message":"..."}],\n'
            '  "suggested_fix":"...",\n'
            '  "polished_text":"..."\n'
            "}\n\n"
            f"Global prompt/context:\n{global_prompt.strip()}\n\n"
            f"Unit input:\n{json.dumps(unit, ensure_ascii=False)}"
        )
        raw = self._request(prompt, temperature=0.2)
        return self._extract_json_object(raw)

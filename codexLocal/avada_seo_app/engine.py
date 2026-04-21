import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .constants import BATHROOM_TERMS, EN_STOPWORDS, GARDEN_TERMS, PL_STOPWORDS
from .models import PageContentResult, Placeholder, StrategyResult
from .openai_client import OpenAIClient
from .parser import AvadaParser


@dataclass
class EngineConfig:
    generation_mode: str = "text-only"
    language_mode: str = "auto"
    link_mode: str = "strict-html-internal"
    format_mode: str = "avada-strict"


class AvadaSeoEngine:
    def __init__(self, log: Optional[Callable[[str], None]] = None) -> None:
        self.parser = AvadaParser()
        self.log = log
        self.template_raw: str = ""
        self.template_path: str = ""
        self.placeholders: List[Placeholder] = []
        self.last_scan_report: Dict[str, int] = {}
        self.section_schema: List[Dict[str, object]] = []
        self.page_strategy: Optional[StrategyResult] = None
        self.page_content: Optional[PageContentResult] = None
        self.last_mapping_report: Dict[str, object] = {}
        self.internal_domains: List[str] = []

    def _log(self, message: str) -> None:
        if self.log:
            self.log(message)

    @staticmethod
    def read_text(path: Path) -> str:
        for encoding in ("utf-8", "cp1250", "latin-1"):
            try:
                return path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
        return path.read_text(errors="replace")

    def load_template(self, path: str) -> Dict[str, int]:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Nie znaleziono pliku: {p}")
        self.template_path = str(p)
        self.template_raw = self.read_text(p)
        parsed = self.parser.parse(self.template_raw)
        self.placeholders = parsed.placeholders
        self.last_scan_report = parsed.counts.copy()
        self.page_strategy = None
        self.page_content = None
        self.last_mapping_report = {}
        domains = re.findall(r"https?://([^/\s\"']+)", self.template_raw, flags=re.IGNORECASE)
        self.internal_domains = sorted(set(domains))
        self.section_schema = self._build_section_schema()
        return self.last_scan_report

    def _build_section_schema(self) -> List[Dict[str, object]]:
        schema: List[Dict[str, object]] = []
        by_section: Dict[int, List[Placeholder]] = {}
        for ph in self.placeholders:
            by_section.setdefault(ph.section_id, []).append(ph)

        if not by_section:
            return schema

        max_section = max(by_section.keys())
        for section_id in sorted(by_section.keys()):
            items = sorted(by_section[section_id], key=lambda x: x.start)
            text_items = [p for p in items if p.kind == "text"]
            image_items = [p for p in items if p.kind == "image"]
            preview = text_items[0].original[:140].replace("\n", " ") if text_items else ""

            content_box_titles = [p.pid for p in text_items if p.block_type == "fusion_content_box" and p.field == "title"]
            content_box_bodies = [p.pid for p in text_items if p.block_type == "fusion_content_box" and p.field == "body"]
            checklist_items = [p.pid for p in text_items if p.block_type == "fusion_li_item"]
            short_texts = [p for p in text_items if p.suggested_words <= 14]
            faq_like = [p for p in text_items if "?" in p.original]

            if checklist_items:
                section_kind = "checklist_section"
            elif content_box_titles or content_box_bodies:
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
            elif section_kind == "checklist_section":
                role_hint = "feature_checklist"
            elif section_kind == "content_box_section":
                role_hint = "selling_points"
            elif len(short_texts) >= 1 and len(text_items) >= 1 and section_id > 1:
                role_hint = "heading_plus_body"
            elif section_id == max_section:
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
                    "checklist_item_ids": checklist_items,
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

    def schema_by_section_id(self) -> Dict[int, Dict[str, object]]:
        return {int(x["section_id"]): x for x in self.section_schema if "section_id" in x}

    def generate_strategy(self, client: OpenAIClient, global_prompt: str) -> StrategyResult:
        strategy = client.generate_page_strategy(global_prompt=global_prompt, section_schema=self.section_schema)
        self.page_strategy = strategy
        self.page_content = None
        return strategy

    def generate_page_content(
        self,
        client: OpenAIClient,
        global_prompt: str,
        config: EngineConfig,
        extra_prompts: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        text_placeholders = [p for p in self.placeholders if p.kind == "text"]
        extra_prompts = extra_prompts or {}

        page_result = client.generate_page_content(
            global_prompt=global_prompt,
            strategy=self.page_strategy,
            section_schema=self.section_schema,
            text_placeholders=text_placeholders,
            language_mode=config.language_mode,
            link_mode=config.link_mode,
            format_mode=config.format_mode,
        )
        self.page_content = page_result

        mapped = page_result.mapped.copy()
        fallback_count = 0
        schema = self.schema_by_section_id()
        for ph in text_placeholders:
            if ph.pid in mapped and mapped[ph.pid].strip():
                continue
            fallback_count += 1
            mapped[ph.pid] = client.generate_text(
                global_prompt=global_prompt,
                placeholder=ph,
                optional_prompt=extra_prompts.get(ph.pid, ""),
                strategy=self.page_strategy,
                section_schema_entry=schema.get(ph.section_id, {}),
                language_mode=config.language_mode,
                link_mode=config.link_mode,
                format_mode=config.format_mode,
            )

        for ph in text_placeholders:
            if ph.pid in mapped and mapped[ph.pid].strip():
                mapped[ph.pid] = self.postprocess_generated_text(ph, mapped[ph.pid], config)

        self.last_mapping_report = {
            "placeholders_requested": len(text_placeholders),
            "generated_keys": len(page_result.mapped),
            "missing_from_page_json": page_result.missing_ids,
            "fallback_count": fallback_count,
            "mapped_success": len([p for p in text_placeholders if mapped.get(p.pid)]),
        }
        return mapped

    def generate_one_text(
        self,
        client: OpenAIClient,
        global_prompt: str,
        placeholder_id: str,
        optional_prompt: str,
        config: EngineConfig,
    ) -> str:
        ph = next((x for x in self.placeholders if x.pid == placeholder_id and x.kind == "text"), None)
        if not ph:
            raise ValueError(f"Nie znaleziono placeholdera tekstowego: {placeholder_id}")
        schema = self.schema_by_section_id().get(ph.section_id, {})
        content = client.generate_text(
            global_prompt=global_prompt,
            placeholder=ph,
            optional_prompt=optional_prompt,
            strategy=self.page_strategy,
            section_schema_entry=schema,
            language_mode=config.language_mode,
            link_mode=config.link_mode,
            format_mode=config.format_mode,
        )
        return self.postprocess_generated_text(ph, content, config)

    def generate_one_image_metadata(
        self,
        client: OpenAIClient,
        global_prompt: str,
        placeholder_id: str,
        image_reference: str,
        optional_prompt: str,
    ) -> Dict[str, str]:
        return client.generate_image_metadata(
            global_prompt=global_prompt,
            placeholder_id=placeholder_id,
            image_reference=image_reference,
            optional_prompt=optional_prompt,
        )

    @staticmethod
    def _normalize_spacing(text: str) -> str:
        value = text.replace("\r\n", "\n").replace("\r", "\n")
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()

    @staticmethod
    def _count_words(value: str) -> int:
        return len(re.findall(r"[A-Za-z0-9'-]+", value))

    @staticmethod
    def _normalize_text(value: str) -> str:
        val = value.replace("\r\n", "\n").replace("\r", "\n").strip().lower()
        return re.sub(r"\s+", " ", val)

    @staticmethod
    def _strip_tags(text: str) -> str:
        return re.sub(r"<[^>]+>", "", text).strip()

    @staticmethod
    def _has_heading_tag(text: str) -> bool:
        return bool(re.search(r"<h([1-6])\b[^>]*>.*?</h\1>", text, flags=re.IGNORECASE | re.DOTALL))

    @staticmethod
    def _first_heading_tag_meta(text: str) -> Optional[Tuple[str, str]]:
        m = re.search(r"<h([1-6])(\b[^>]*)>.*?</h\1>", text, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            return None
        return m.group(1), (m.group(2) or "")

    @staticmethod
    def _source_heading_word_count(text: str) -> int:
        m = re.search(r"<h([1-6])\b[^>]*>(.*?)</h\1>", text, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            return 0
        inner = AvadaSeoEngine._strip_tags(m.group(2))
        return len(re.findall(r"[A-Za-z0-9'-]+", inner))

    @staticmethod
    def _has_heading_like_pattern(text: str) -> bool:
        if AvadaSeoEngine._has_heading_tag(text):
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

            level, attrs = self._first_heading_tag_meta(source) or ("2", "")
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
        return f"{lines[0]}\n\n{' '.join(lines[1:])}".strip()

    def _enforce_internal_html_links(self, text: str, config: EngineConfig) -> str:
        if config.link_mode != "strict-html-internal":
            return text

        def is_internal(url: str) -> bool:
            if url.startswith("/"):
                return True
            for d in self.internal_domains:
                if d and d in url:
                    return True
            return False

        def repl_md(match: re.Match[str]) -> str:
            label = match.group(1).strip()
            url = match.group(2).strip()
            if is_internal(url):
                return f'<a href="{url}">{label}</a>'
            return match.group(0)

        return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", repl_md, text)

    def postprocess_generated_text(self, ph: Placeholder, text: str, config: EngineConfig) -> str:
        out = text.strip()
        if config.format_mode == "avada-strict":
            out = self._preserve_heading_layout(ph.original, out)
        else:
            out = self._normalize_spacing(out)
        out = self._enforce_internal_html_links(out, config)
        return out

    def checklist_review(
        self,
        replacements: Optional[Dict[str, str]],
        global_prompt: str,
    ) -> Dict[str, object]:
        checklist_placeholders = [p for p in self.placeholders if p.kind == "text" and p.block_type == "fusion_li_item"]
        if not checklist_placeholders:
            return {"total_items": 0, "flagged_items": 0, "items": []}

        source_terms = self._source_topic_terms(limit=30)
        target_terms = set(self._target_terms_from_prompt(global_prompt))
        contamination_terms = [t for t in source_terms if t not in target_terms]
        contamination_terms.extend([kw for kw in GARDEN_TERMS if kw not in target_terms])
        ignore_terms = {
            "href",
            "https",
            "http",
            "www",
            "com",
            "co",
            "uk",
            "span",
            "style",
            "strong",
            "data",
            "start",
            "end",
            "center",
            "left",
            "right",
            "color",
            "proper",
            "durable",
            "system",
            "systems",
        }
        domain_tokens = set(re.findall(r"[a-z0-9-]{3,}", " ".join(self.internal_domains).lower()))
        contamination_terms = sorted(
            set(t for t in contamination_terms if t not in ignore_terms and t not in domain_tokens)
        )

        review_items: List[Dict[str, object]] = []
        for ph in checklist_placeholders:
            value = replacements.get(ph.pid, ph.original) if replacements else ph.original
            low = value.lower()
            hits = [t for t in contamination_terms if re.search(rf"\b{re.escape(t)}\b", low)]
            review_items.append(
                {
                    "pid": ph.pid,
                    "section_id": ph.section_id,
                    "text": value.strip(),
                    "changed": self._normalize_text(value) != self._normalize_text(ph.original),
                    "contamination_terms": hits[:12],
                }
            )

        flagged = len([x for x in review_items if x["contamination_terms"]])
        return {
            "total_items": len(review_items),
            "flagged_items": flagged,
            "items": review_items,
        }

    @staticmethod
    def _is_link_block_style(text: str) -> bool:
        lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()]
        if len(lines) < 3:
            return False
        anchor_lines = [ln for ln in lines if "<a " in ln.lower() and "href=" in ln.lower()]
        if len(anchor_lines) < 3:
            return False
        contextual = 0
        for ln in anchor_lines:
            words = re.findall(r"[A-Za-z0-9'-]+", re.sub(r"<[^>]+>", " ", ln))
            if len(words) >= 8:
                contextual += 1
        return contextual <= 1

    def build_output(self, replacements: Dict[str, str]) -> Tuple[str, Dict[str, str]]:
        if not self.template_raw or not self.placeholders:
            return "", {}
        chunks: List[str] = []
        final_replacements: Dict[str, str] = {}
        last = 0
        for ph in sorted(self.placeholders, key=lambda x: x.start):
            value = (replacements.get(ph.pid) or "").strip()
            if not value:
                value = ph.original
            final_replacements[ph.pid] = value
            chunks.append(self.template_raw[last: ph.start])
            chunks.append(value)
            last = ph.end
        chunks.append(self.template_raw[last:])
        return "".join(chunks), final_replacements

    @staticmethod
    def _tokenize_keywords(value: str) -> List[str]:
        tokens = [t.lower() for t in re.findall(r"[A-Za-z0-9'-]{3,}", value)]
        return [t for t in tokens if t not in PL_STOPWORDS and t not in EN_STOPWORDS]

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

    @staticmethod
    def _target_terms_from_prompt(global_prompt: str) -> List[str]:
        prompt = global_prompt.lower()
        tokens = [t for t in re.findall(r"[a-z0-9-]{4,}", prompt) if t not in PL_STOPWORDS and t not in EN_STOPWORDS]
        return sorted(set(tokens))

    @staticmethod
    def _detect_lang(text: str) -> str:
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

    @staticmethod
    def _required_language(global_prompt: str, mode: str) -> str:
        mode = (mode or "auto").strip().lower()
        if mode == "english-only":
            return "en"
        if mode == "polish-only":
            return "pl"
        prompt = global_prompt.lower()
        if any(x in prompt for x in ["english only", "only english", "write in english"]):
            return "en"
        if any(x in prompt for x in ["tylko po polsku", "po polsku", "polish only"]):
            return "pl"
        return "any"

    def validate_output_text(
        self,
        output: str,
        replacements: Optional[Dict[str, str]],
        global_prompt: str,
        config: EngineConfig,
    ) -> Dict[str, object]:
        issues: List[str] = []
        warnings: List[str] = []

        marker_hits = re.findall(r"\b(?:__)?(?:TEXT|IMG|CBTITLE|CBBODY)_\d{3}(?:__)?\b", output)
        if marker_hits:
            issues.append(f"Wykryto niepodmienione markery: {', '.join(sorted(set(marker_hits))[:20])}")

        lower_output = output.lower()
        found_old_curated = [kw for kw in GARDEN_TERMS if kw in lower_output]
        if found_old_curated:
            warnings.append("Wykryto stare frazy domenowe: " + ", ".join(found_old_curated[:8]))

        source_terms = self._source_topic_terms()
        target_terms = self._target_terms_from_prompt(global_prompt)
        source_terms_not_target = [t for t in source_terms if t not in target_terms]
        source_hits = [t for t in source_terms_not_target if re.search(rf"\b{re.escape(t)}\b", lower_output)]
        if len(source_hits) >= 8:
            issues.append("Silna kontaminacja semantyczna ze zrodla: " + ", ".join(source_hits[:12]))
        elif len(source_hits) >= 4:
            warnings.append("Wykryto dziedziczenie konceptow zrodlowych: " + ", ".join(source_hits[:10]))

        output_text_blocks = [p for p in self.parser.parse(output).placeholders if p.kind == "text"]
        langs = [self._detect_lang(p.original) for p in output_text_blocks if p.original.strip()]
        en_blocks = sum(1 for x in langs if x == "en")
        mixed_blocks = sum(1 for x in langs if x == "mixed")
        required_lang = self._required_language(global_prompt, config.language_mode)

        link_block_pids = [p.pid for p in output_text_blocks if self._is_link_block_style(p.original)]
        if link_block_pids:
            warnings.append(
                "Wykryto sekcje wygladajace jak blok samych linkow (nienaturalne linkowanie): "
                + ", ".join(link_block_pids[:12])
            )

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

            checklist_qc = self.checklist_review(replacements, global_prompt)
            if checklist_qc["flagged_items"]:
                warnings.append(
                    f"Checklist contamination: {checklist_qc['flagged_items']}/{checklist_qc['total_items']} pozycji ma stare terminy."
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

        if replacements is not None:
            image_placeholders = [p for p in self.placeholders if p.kind == "image"]
            unchanged_images = []
            for ph in image_placeholders:
                rep = replacements.get(ph.pid, ph.original)
                if self._normalize_text(rep) == self._normalize_text(ph.original):
                    unchanged_images.append(ph.pid)
            if config.generation_mode == "text+image" and unchanged_images:
                warnings.append("Tryb text+image, ale niezmienione obrazy: " + ", ".join(unchanged_images[:20]))

        changed = 0
        if replacements:
            for ph in self.placeholders:
                value = replacements.get(ph.pid, ph.original)
                if self._normalize_text(value) != self._normalize_text(ph.original):
                    changed += 1

        report_lines = ["AVADA Validation Report", "======================", "", "Elementy wykryte podczas skanowania:"]
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

    def build_generation_report(self, replacements: Dict[str, str], config: EngineConfig) -> str:
        lines = [
            "AVADA Generation Report",
            "======================",
            "",
            "Runtime modes:",
            f"- generation_mode: {config.generation_mode}",
            f"- language_mode: {config.language_mode}",
            f"- link_mode: {config.link_mode}",
            f"- format_mode: {config.format_mode}",
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

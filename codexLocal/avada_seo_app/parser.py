import re
from typing import Dict, List, Tuple

from .models import ParseResult, Placeholder


class AvadaParser:
    FUSION_TEXT_RE = re.compile(r"\[fusion_text\b[^\]]*\](.*?)\[/fusion_text\]", re.IGNORECASE | re.DOTALL)
    FUSION_IMAGEFRAME_RE = re.compile(
        r"\[fusion_imageframe\b[^\]]*\](.*?)\[/fusion_imageframe\]", re.IGNORECASE | re.DOTALL
    )
    FUSION_CONTENT_BOX_RE = re.compile(
        r"\[fusion_content_box\b([^\]]*)\](.*?)\[/fusion_content_box\]", re.IGNORECASE | re.DOTALL
    )
    FUSION_LI_ITEM_RE = re.compile(
        r"\[fusion_li_item\b[^\]]*\](.*?)\[/fusion_li_item\]", re.IGNORECASE | re.DOTALL
    )
    FUSION_IMAGE_RE = re.compile(r"\[fusion_image\b([^\]]*?)\s*/\]", re.IGNORECASE | re.DOTALL)

    TITLE_ATTR_RE = re.compile(r'title\s*=\s*"([^"]*)"', re.IGNORECASE | re.DOTALL)
    IMAGE_ATTR_RE = re.compile(r'image\s*=\s*"([^"]*)"', re.IGNORECASE | re.DOTALL)

    CONTAINER_TOKEN_RE = re.compile(
        r"\[fusion_builder_container\b[^\]]*\]|\[/fusion_builder_container\]", re.IGNORECASE
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
            "fusion_checklist_item": 0,
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

        for m in self.FUSION_LI_ITEM_RE.finditer(raw):
            section_id = self._section_for_pos(m.start(), sections)
            placeholders.append(
                Placeholder(
                    pid=f"TEXT_{text_idx:03d}",
                    kind="text",
                    block_type="fusion_li_item",
                    field="inner",
                    original=m.group(1).strip(),
                    suggested_words=max(3, self._word_count(m.group(1))),
                    start=m.start(1),
                    end=m.end(1),
                    section_id=section_id,
                )
            )
            counts["fusion_checklist_item"] += 1
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

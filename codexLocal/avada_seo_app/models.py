from dataclasses import dataclass
from typing import Dict, List


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
    in_gallery: bool = False


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

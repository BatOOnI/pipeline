from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class Observation:
    ok: bool
    summary: str
    changed: bool = False
    details: str = ""
    tool: str = ""
    path: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

from dataclasses import asdict, dataclass, field
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


@dataclass
class ToolResultEnvelope:
    schema: str = "tool_result_envelope/v1"
    tool_name: str = ""
    action_type: str = ""
    ok: bool = False
    changed: bool = False
    summary: str = ""
    details_preview: str = ""
    path: str = ""
    mode: str = ""
    task_shape: str = ""
    provider: str = ""
    iteration: int = 0
    staged: bool = False
    timestamp_ms: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

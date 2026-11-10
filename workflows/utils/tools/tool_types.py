from dataclasses import dataclass, field
from typing import Optional, Any

@dataclass
class ToolResult:
    """This is the result of Tool execution """
    id:        str
    result:    Optional[Any] = None
    error: Optional[str] = None

@dataclass
class ToolCallChunk:
    """This is a chunk of tool call Openai style """
    id:        str
    name:      str
    arguments: str  # raw JSON string, parsed by executor

@dataclass
class CodeChunk:
    """This is a chunk of code Openai style """
    id:        str
    code:      str
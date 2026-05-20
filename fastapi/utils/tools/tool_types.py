from dataclasses import dataclass, field

@dataclass
class ToolCallChunk:
    """This is a chunk of tool call Openai style """
    id:        str
    name:      str
    arguments: str  # raw JSON string, parsed by executor
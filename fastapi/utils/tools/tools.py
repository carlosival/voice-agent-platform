
from typing import Any
from smolagents import Tool
from signals import EndOfStream

class EndConversationTool(Tool):
    name = "end_conversation"
    description = "Use this tool when you considered the conversation is finished."
    inputs = {}  # required even if empty
    output_type = "any"

    def forward(self) -> Any:
        return EndOfStream()
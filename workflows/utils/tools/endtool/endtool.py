
from typing import Any
from smolagents import Tool
from workflows.signals import EndOfStream

class EndConversationTool(Tool):
    name = "end_conversation"
    description = "Use this tool when you considered the conversation is finished."
    inputs = {
        "confirm": {
            "type": "boolean",
            "description": "Set to True to confirm closing the chat session."
        }
        }  # required even if empty
    output_type = "any"

    def forward(self, confirm: bool = True) -> Any:
        return EndOfStream()
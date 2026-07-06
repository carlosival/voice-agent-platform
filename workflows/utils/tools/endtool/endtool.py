
from typing import Any, Union
from smolagents import Tool
from workflows.signals import EndOfStream

class EndConversationTool(Tool):
    name = "end_conversation"
    description = "Use this tool when you considered the conversation is finished."
    inputs = {
        "confirm": {
            "type": "boolean",
            "description": "Set to True to confirm closing the chat session.",
            "nullable": True # <--- Add this line to satisfy the validator
        }
        }  # required even if empty
    output_type = "any"

    def forward(self, confirm: Union[bool, str] = True) -> Any:
        # Robust parsing: handle if LLM passes string "true", "True", "false", etc.
        if isinstance(confirm, str):
            logger.warning(f"[EndConversationTool] Received 'confirm' as string: {repr(confirm)}. Normalizing...")
            confirm = confirm.lower().strip() in ("true", "1", "yes")
        return EndOfStream()
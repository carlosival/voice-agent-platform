
from typing import Any, Union
from smolagents import Tool
from workflows.signals import EndOfStream
import logging
logger = logging.getLogger(__name__)


class EndConversationTool(Tool):
    name = "end_conversation"
    description = "Use this tool when you considered the conversation is finished."
    inputs = {
        "confirm": {
            "type": "string",
            "description": "Set to True to confirm closing the chat session.",
            "nullable": True
        }
        }  # required even if empty
    output_type = "any"

    def forward(self, confirm: Any = "true") -> Any:
        # Pydantic will now pass whatever the LLM sent directly into here
        if isinstance(confirm, str):
            confirm_bool = confirm.lower().strip() in ("true", "1", "yes")
        elif isinstance(confirm, bool):
            confirm_bool = confirm
        else:
            confirm_bool = True
            
        if confirm_bool:
            logger.info("[EndConversationTool] Tool executed successfully, sending EndOfStream signal.")
            return EndOfStream()
            
        return "Conversation continuation requested."
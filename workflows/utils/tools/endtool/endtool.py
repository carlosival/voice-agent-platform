
from typing import Any, Union
from smolagents import Tool
from workflows.signals import EndOfStream
import logging
logger = logging.getLogger(__name__)


class EndConversationTool(Tool):
    name = "end_conversation"
    description = "Use this tool when you consider the conversation is finished."
    inputs = {
        "confirm": {
            "type": "string",
            "description": 'Use "true" to confirm closing the chat session, "false" to keep it open.',
            "nullable": True,
        }
    }
    output_type = "any"

    def __init__(self, config: dict | None = None, **kwargs):
        super().__init__(**kwargs)
        self._cfg = config or {}
        

    def forward(self, confirm: str | None = "true") -> Any:
        # None (omitted by the LLM) means "yes"; otherwise only explicit yes-values end the call
        value = "true" if confirm is None else str(confirm).strip().lower()

        if value in ("true", "1", "yes"):
            logger.info("[EndConversationTool] Sending EndOfStream signal.")
            return EndOfStream()

        return "Conversation continuation requested."
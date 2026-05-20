import os
from typing import Optional

# This file isolates the vendor library
from langfuse import Langfuse 


class GenerationSpanWrapper:
    def __init__(self, generation_obj):
        self._gen = generation_obj
        self._accumulated_content = ""
        self._accumulated_tool_calls = []

    def append_token(self, token: str):
        self._accumulated_content += token

    def append_tool_call(self, tool_call_data: dict):
        self._accumulated_tool_calls.append(tool_call_data)

    def end(self, finish_reason: str):
        # Package out final data structures cleanly
        output_data = {
            "finish_reason": finish_reason,
            "text": self._accumulated_content,
        }
        if self._accumulated_tool_calls:
            output_data["tool_calls"] = self._accumulated_tool_calls
            
        self._gen.end(output=output_data)

    def fail(self, error: Exception):
        self._gen.end(
            level="ERROR",
            status_message=str(error),
            output={"error_type": type(error).__name__}
        )


class VoicePipelineTracer:
    def __init__(self):
        # Easily switch this out for Weave, Phoenix, etc.
        self.client = Langfuse(
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
            secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
            host=os.getenv("LANGFUSE_HOST")
        )

    def start_trace(self, name: str, session_id: str):
        # Returns a generic context or trace wrapper
        return self.client.trace(name=name, session_id=session_id)

    def log_span(self, name: str, input_data: dict, output_data: Optional[str] = None):
        span = self.client.span(name=name, input=input_data)
        if output_data:
            span.end(output=output_data)
        return span

    def create_generation_span(self, model_name: str, messages: list, tools: Optional[list] = None):
        """
        Creates and returns a wrapper object for tracking a specific LLM execution.
        """
        # Create a low-level generation span
        generation = self.client.generation(
            name="LLM-Streaming-Turn",
            model=model_name,
            input=messages,
            model_parameters={
                "temperature": 0,
                "tools_provided": len(tools) if tools else 0
            }
        )
        return GenerationSpanWrapper(generation)
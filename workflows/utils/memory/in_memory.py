from typing import List, Dict, Optional
from .interfaces import Memory
import asyncio

class InMemoryMemory(Memory):
    def __init__(self, in_memory: Optional[List[Dict]] = None, message_limit = 100):
        self.in_memory = in_memory or []
        self.lock = asyncio.Lock()
        self.message_limit = message_limit
        
    async def set_memory(self, in_memory: List[Dict]) -> None:
        async with self.lock:
            self.in_memory = in_memory

    async def add_user_message(self, message: str) -> None:
        if not message or not isinstance(message, str):
            raise ValueError("message must be a non-empty string")
        await self._add_message("user", message)

    async def add_ai_message(self, message: str, action: Optional[str] = None) -> None:
        if not message or not isinstance(message, str):
            raise ValueError("message must be a non-empty string")
        if action and not isinstance(action, str):
            raise ValueError("action must be a non-empty string")
        await self._add_message("assistant", message, action)

    async def add_tool_result(self, tool_result: Dict[str, str]) -> None:
        if not tool_result or not isinstance(tool_result, dict):
            raise ValueError("tool_result must be a dictionary")

        idetf = tool_result.get("id", None)
        result = tool_result.get("result", None)
        error = tool_result.get("error", None)

        if not idetf or not isinstance(idetf, str):
            raise ValueError("tool_result must have an id")
        if not result and not error:
            raise ValueError("tool_result must have a result or error")
        if result:
            entry = {"role": "tool", "tool_call_id": idetf, "result": result}    
            async with self.lock:
                self.in_memory.append(entry)
                return
        if error:
            logger.error(f"Error in tool {idetf}: {error}")
            
    async def add_tools_results(self, tool_results: List[Dict[str, str]]) -> None:
        if not tool_results or not isinstance(tool_results, list):
            raise ValueError("tool_results must be a non-empty list")
        for tool_result in tool_results:
            await self.add_tool_result(tool_result)
    
    async def add_tools_calls(self, tool_calls: List[Dict[str, str]]) -> None:
        if not tool_calls or not isinstance(tool_calls, list):
            raise ValueError("tool_calls must be a non-empty list")
        for tool_call in tool_calls:
            if not tool_call or not isinstance(tool_call, dict):
                raise ValueError("tool_call must be a dictionary")
            if "id" not in tool_call or not isinstance(tool_call["id"], str):
                raise ValueError("tool_call must have an id")
            if "name" not in tool_call or not isinstance(tool_call["name"], str):
                raise ValueError("tool_call must have a name")
            if "arguments" not in tool_call or not isinstance(tool_call["arguments"], str):
                raise ValueError("tool_call must have arguments")
        entrys = [{
            "id": tc["id"],        #🔒  must be "id"  (matched by tool result below)
            "type": "function",   #🔒  must be "function" (only supported type)
            "function": {         #🔒  must be "function"
                "name": tc["name"],       #🔒  must be "name"
                "arguments": tc["arguments"], # 🔒  must be "arguments" (JSON string)
            },
        } for tc in tool_calls] 
        async with self.lock:
            self.in_memory.extend(entrys)

    async def _add_message(self, role: str, content: str, action: Optional[str] = None) -> None:
        if role == "user":
            entry = {"role": role, "content": content}
        else:
            entry = {"role": role, "action": action, "content": content}
        async with self.lock:
            self.in_memory.append(entry)

    async def get_messages(self) -> List[Dict[str, str]]:
        async with self.lock:

            if self.message_limit is not None and self.message_limit > 0:

                # defensive copy
                return list(self.in_memory[-self.message_limit:])

            return list(self.in_memory)
        
    
    async def rewind_last_message_x(self, x: int = 1) -> None:
        async with self.lock:
            self.in_memory = self.in_memory[:-x]

    async def clear(self) -> None:
        async with self.lock:
            self.in_memory.clear()
    
    async def get_message_count(self) -> int:
        async with self.lock:
            return len(self.in_memory)

from typing import Protocol, List, Dict
from ..tools import ToolCallChunk, ToolResult

# Fix and check all function signature

class IMemory(Protocol):
    
    async def add_user_message(self, message: str)-> bool:
        ...
        
    async def add_ai_message(self,message: str, action: str )-> bool:
        ...
        

    async def add_tools_results(self, results: List[ToolResult], tool_call_id: str)-> bool:
        ...

    async def add_tools_calls(self, tools_calls: List[ToolCallChunk])-> None:
        ...

    async def get_messages(self) -> List[Dict[str, str]]:
        ...

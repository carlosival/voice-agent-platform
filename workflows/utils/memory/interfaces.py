
from typing import Protocol, List, Dict

class Memory(Protocol):
    
    async def add_user_message(self, message: str)-> bool:
        ...
        
    async def add_ai_message(self,message: str, action: str )-> bool:
        ...
        

    async def get_messages(self) -> List[Dict[str, str]]:
        ...

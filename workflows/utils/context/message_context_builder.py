from workflows.utils.memory import IMemory  

def _extract_messages(self, messages: list) -> str:
        """Build conversation context from messages"""
        try:
            context_parts = []
            for msg in messages:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                action = msg.get("action", None)
                if not action or action == "respond_to_user":
                    context_parts.append(f"{role}: {content}")
            return "\n".join(context_parts)
        except Exception as e:
            logger.error(f"Error building context: {e}")
            return ""

async def build_chat_messages(memory: IMemory, system_prompt: str, user_input: str = None) -> list:
    """
    Build a list of message objects for the OpenAI-compatible Chat API.
    """
    # Start with the system prompt as the first message
    chat_history = [{"role": "system", "content": system_prompt.strip()}]
    
    # message
    messages = await memory.get_messages()

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")
        action = msg.get("action", None)
        
        # Only include relevant messages (filtering logic)
        if not action:
            chat_history.append({"role": role, "content": content})
    
    if user_input:
        chat_history.append({"role": "user", "content": user_input})
        # Don't sync chat history with user input here,
        # If something goes wrong, or it is cancelled, we don't want to save the message
        # We will sync it in the worker after the response is generated.
 
    return chat_history


def context_from_messages(messages: list) -> str:
    """Build full prompt for LLM from system prompt and messages"""
    return f"{system_prompt}\n\n{_extract_messages(messages)}"
   
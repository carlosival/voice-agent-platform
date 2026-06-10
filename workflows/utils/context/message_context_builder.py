SYSTEM_PROMPT = """
Tu eres un asistente de voz en español.
No uses markdown, no uses listas.
Se conciso.
Responde con 1 a 3 oraciones
No inventes referencias.
Si no estás seguro, di que no sabes.
Responde solo a lo que el usuario dijo claramente.
"""


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

def build_chat_messages(messages: list) -> list:
    """
    Build a list of message objects for the OpenAI-compatible Chat API.
    """
    # Start with the system prompt as the first message
    chat_history = [{"role": "system", "content": SYSTEM_PROMPT.strip()}]
    
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")
        action = msg.get("action", None)
        
        # Only include relevant messages (filtering logic)
        if not action:
            chat_history.append({"role": role, "content": content})
                
    return chat_history


def context_from_messages(messages: list) -> str:
    """Build full prompt for LLM from system prompt and messages"""
    return f"{system_prompt}\n\n{_extract_messages(messages)}"
   
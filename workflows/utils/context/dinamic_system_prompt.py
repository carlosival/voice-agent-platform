import json

class DynamicVoiceSystemPrompt:
    def __init__(self):
        # 1. CORE SYSTEM DIRECTIVES (Never modified by endpoints)
        self.base_directives = """
Eres un asistente de voz en español amigable y eficiente.
[REGLAS ESTRICTAS DE AUDIO]
- No utilices formato Markdown (*, #, _, lists).
- Sé extremadamente conciso. Responde siempre entre 1 y 3 oraciones como máximo para minimizar la latencia.
- No inventes información. Si no sabes algo, di que no lo sabes.
- Responde únicamente a lo que el usuario exprese claramente.
- Nunca escribas etiquetas de ningún tipo en tu respuesta de texto final.
"""

    def compile(self, custom_user_info: dict = None, tools_override: list = None) -> str:
        """
        Assembles the final system prompt based on real-time call injections.
        """
        compiled_prompt = self.base_directives

        # 2. DYNAMIC STATE REGISTER (Injecting arbitrary CRM / Session data)
        if custom_user_info:
            compiled_prompt += "\n[CONTEXTO DINÁMICO DEL USUARIO]\n"
            for key, value in custom_user_info.items():
                # Formats variables elegantly so the LLM reads them cleanly
                compiled_prompt += f"- {key.replace('_', ' ').title()}: {value}\n"
            
            # Actionable instruction forcing the LLM to respect the data injected above
            compiled_prompt += "- NOTA: Integra estos datos orgánicamente en la conversación si el contexto lo amerita.\n"

        # 3. EPHEMERAL TOOL INJECTOR (Informing the prompt about injected tool permissions)
        if tools_override:
            compiled_prompt += "\n[HERRAMIENTAS OPERATIVAS DISPONIBLES]\n"
            compiled_prompt += "Tienes autorización explícita para invocar las siguientes funciones si la situación lo requiere:\n"
            for tool in tools_override:
                compiled_prompt += f"- `{tool.get('name')}`: {tool.get('description')}\n"
            
            compiled_prompt += "- NOTA: Cuando se cumplan las condiciones de una herramienta, invócala de inmediato usando el protocolo del sistema.\n"
        else:
            # Default fallback tool directive if nothing gets passed
            compiled_prompt += "\n- Si la conversación concluyó, invoca la herramienta 'end_conversation'.\n"

        return compiled_prompt
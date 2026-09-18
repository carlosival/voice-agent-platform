url_provider = {
    "ollama": "http://localhost:11434/v1",
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta",
    "xai": "https://api.xai.com/v1",
}


provider_models_supported = {
    "openai": {
        "gpt-3.5-turbo": 1,
        
    },
    "groq": {
        "openai/gpt-oss-120b": 1,
        "llama-3.3-70b-versatile":1,
    },
    "anthropic": 2,
    "google": 3,
    "xai": 4,
}



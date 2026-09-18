from typing import Any

def build_payload(
    provider: str,
    text: str,
    model: str,
    voice: str,
    speed: float | None = None,
) -> dict[str, Any]:

    payload = {
        "model": model,
        "input": text,
        "voice": voice,
    }

    if speed is not None:
        payload["speed"] = speed

    match provider:
        case "openai":
            pass

        case "speaches":
            payload["response_format"] = "pcm"
            
        case _:
            raise ValueError(f"Unsupported TTS provider: {provider}")

    return payload

def get_tts_provider_url(provider_name: str):
    providers = {
        "speaches": "http://speaches:8000"
        # TODO: Add more providers as needed
    }

    provider = providers.get(provider_name, None)

    if provider:
        return provider
    else:
        raise ValueError(f"Unknown STT provider: {provider_name}")
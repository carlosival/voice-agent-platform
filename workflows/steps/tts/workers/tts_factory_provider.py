'''
Factory for TTS providers
'''

from .tts_provider_openai import tts_worker
## TODO: Add more providers as needed




def get_tts_provider_worker(provider_name: str):
    providers = {
        "openai": tts_worker,
        "speaches": tts_worker,
        "groq": tts_worker
        # TODO: Add more providers as needed
    }

    provider = providers.get(provider_name, None)

    if provider:
        return provider
    else:
        raise ValueError(f"Unknown TTS provider: {provider_name}")
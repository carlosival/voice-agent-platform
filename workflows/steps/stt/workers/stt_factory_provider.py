'''
Factory for STT providers
'''

from .stt_provider_openai import call_stt
## TODO: Add more providers as needed


def get_stt_provider_url(provider_name: str):
    providers = {
        "groq": "https://api.groq.com/openai",
        "speaches": "http://speaches:8000"
        # TODO: Add more providers as needed
    }

    provider = providers.get(provider_name, None)

    if provider:
        return provider
    else:
        raise ValueError(f"Unknown STT provider: {provider_name}")


def get_stt_provider(provider_name: str):
    providers = {
        "openai": call_stt,
        "groq": call_stt,

        # TODO: Add more providers as needed
    }

    provider = providers.get(provider_name, None)

    if provider:
        return provider
    else:
        raise ValueError(f"Unknown STT provider: {provider_name}")

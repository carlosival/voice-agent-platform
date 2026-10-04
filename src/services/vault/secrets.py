
from typing import Callable
import os


VaultSecretGetter = Callable[[str, str], str | None]


class Secrets:

    def __init__(self, vault_getter: VaultSecretGetter):
        self._vault_getter = vault_getter

    def get_secret(self, path: str, key: str) -> str:
        value = self._vault_getter(path, key)

        if value is None:
            value = os.getenv(key)

        if value is None:
            raise KeyError(f"Secret not found: {key}")

        return value
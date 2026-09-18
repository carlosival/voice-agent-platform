from typing import Protocol
import numpy as np


class IAudioOutput(Protocol):

    async def write(self, pcm: np.ndarray) -> None:
        """
        Write mono PCM S16 audio at 48 kHz.
        """
        ...

    async def add_silence(self, duration_ms: int = 150) -> None:
        ...

    def clear(self) -> None:
        ...

    def stop(self) -> None:
        ...
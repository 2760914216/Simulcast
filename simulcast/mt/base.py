from __future__ import annotations

from typing import AsyncIterator, Protocol


class Translator(Protocol):
    async def stream(
        self,
        text: str,
        history: list[str] | None = None,
        draft: bool = False,
    ) -> AsyncIterator[str]: ...

    async def healthy(self) -> bool: ...

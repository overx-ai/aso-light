from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date


@dataclass
class IndexRecord:
    territory_code: str
    value: float  # multiplier/coefficient relative to base (US = 1.0)
    reference_date: date


class IndexFetcher(ABC):
    """Base class for economic index data fetchers."""

    @property
    @abstractmethod
    def index_type(self) -> str:
        """The index type identifier (ppp, bigmac, netflix, spotify)."""
        ...

    @abstractmethod
    async def fetch(self) -> list[IndexRecord]:
        """Fetch latest index data for all available territories."""
        ...

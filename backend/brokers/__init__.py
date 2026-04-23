from abc import ABC, abstractmethod
from backend.models import Position


class BaseBroker(ABC):
    """Interface that all broker integrations must implement."""

    @abstractmethod
    async def connect(self) -> bool:
        """Authenticate with the broker. Returns True if successful."""
        ...

    @abstractmethod
    async def get_positions(self) -> list[Position]:
        """Fetch current positions from the broker."""
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if currently authenticated."""
        ...

    @property
    @abstractmethod
    def broker_name(self) -> str:
        ...

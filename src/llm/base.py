"""Base interface for LLM providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMResponse:
    """Standardized response from any LLM provider."""

    text: str
    provider: str
    model: str
    tokens_used: int
    latency_ms: float
    cost_estimate: float


class BaseLLMProvider(ABC):
    """Abstract base class for all LLM providers."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        json_mode: bool = False,
    ) -> LLMResponse:
        """Generate a response from the LLM.

        Args:
            prompt: The user prompt to send.
            system_prompt: Optional system-level instruction.
            json_mode: If True, request JSON-formatted output.

        Returns:
            LLMResponse with the generated text and metadata.
        """
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """Check whether this provider is configured and reachable."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release any resources held by the provider."""
        ...

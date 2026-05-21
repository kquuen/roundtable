"""Phase 5: LLM Provider Adapter."""

from __future__ import annotations


class ProviderAdapter:
    """Abstract LLM provider adapter.

    POC mode: returns mock responses.
    Production mode: wraps httpx calls to DeepSeek/OpenAI/Qwen APIs.
    """

    def __init__(self, provider: str = "mock"):
        self.provider = provider

    def chat(self, system_prompt: str, user_message: str, max_tokens: int = 2000) -> str:
        """Send a chat completion request.

        In POC mode, returns a mock response indicating the call would succeed.
        In production, connects to the real provider API.
        """
        if self.provider == "mock":
            return (
                f'{{"summary": "Mock agent analysis for: {user_message[:50]}...",'
                f'"claims": [], "open_questions": [], "recommended_next_actions": []}}'
            )
        # Future: httpx call to provider API
        raise NotImplementedError(f"Provider '{self.provider}' not yet implemented.")


def get_provider(provider: str = "mock") -> ProviderAdapter:
    """Factory: return a ProviderAdapter for the given provider name."""
    return ProviderAdapter(provider=provider)

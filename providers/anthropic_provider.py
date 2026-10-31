"""Anthropic Claude adapter using the official SDK."""

from anthropic import Anthropic

from providers.base import SYSTEM_INSTRUCTIONS


class AnthropicProvider:
    def __init__(self, api_key: str, model: str):
        self._client = Anthropic(api_key=api_key)
        self.model = model

    def evaluate(self, prompt: str) -> str:
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=SYSTEM_INSTRUCTIONS,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(
                block.text for block in response.content if getattr(block, "type", None) == "text"
            )
            if not text:
                raise RuntimeError("The provider returned an empty response.")
            return text
        except Exception:
            raise RuntimeError(
                "Anthropic evaluation failed. Verify the model, credentials, quota, and connectivity."
            ) from None

    def test_connection(self) -> None:
        try:
            self._client.models.retrieve(self.model)
        except Exception:
            raise RuntimeError(
                "Anthropic connection test failed. Verify the model and credentials."
            ) from None

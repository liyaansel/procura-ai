"""OpenAI Chat Completions adapter using the official SDK."""

from openai import OpenAI

from providers.base import SYSTEM_INSTRUCTIONS


class OpenAIProvider:
    def __init__(self, api_key: str, model: str):
        self._client = OpenAI(api_key=api_key)
        self.model = model

    def evaluate(self, prompt: str) -> str:
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_INSTRUCTIONS},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content
            if not content:
                raise RuntimeError("The provider returned an empty response.")
            return content
        except Exception:
            raise RuntimeError(
                "OpenAI evaluation failed. Verify the model, credentials, quota, and connectivity."
            ) from None

    def test_connection(self) -> None:
        try:
            self._client.models.retrieve(self.model)
        except Exception:
            raise RuntimeError(
                "OpenAI connection test failed. Verify the model and credentials."
            ) from None

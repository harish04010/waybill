"""
Thin LLM layer.

Every agent decision goes through `LLM.decide()`, which asks for JSON back.
There are two backends:

- AnthropicLLM calls the Messages API when ANTHROPIC_API_KEY is set.
- MockLLM runs a hand-written policy instead, so the whole system works
  offline, in CI, and on a free hosted demo without burning tokens.

Agents pass their mock policy alongside the prompt, which keeps the two
paths side by side in the code and easy to compare.
"""
import asyncio
import json
import re
from typing import Callable

import httpx

from .world import TransientError


class LLM:
    name = "base"

    async def decide(self, *, system: str, prompt: str, mock: Callable[[], dict]) -> dict:
        raise NotImplementedError


class MockLLM(LLM):
    name = "mock"

    def __init__(self, sim_speed: float = 1.0):
        self.sim_speed = sim_speed

    async def decide(self, *, system: str, prompt: str, mock: Callable[[], dict]) -> dict:
        await asyncio.sleep(0.15 / self.sim_speed)  # pretend to think
        return mock()


class AnthropicLLM(LLM):
    name = "anthropic"
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str, model: str, timeout: float = 30.0):
        self.api_key = api_key
        self.model = model
        self.client = httpx.AsyncClient(timeout=timeout)

    async def decide(self, *, system: str, prompt: str, mock: Callable[[], dict]) -> dict:
        try:
            resp = await self.client.post(
                self.URL,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 600,
                    "system": system + "\n\nRespond with a single JSON object and nothing else.",
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
        except httpx.HTTPError as e:
            raise TransientError(f"LLM request failed: {e}") from e

        if resp.status_code in (429, 500, 502, 503, 529):
            raise TransientError(f"LLM returned {resp.status_code}")
        resp.raise_for_status()
        text = "".join(b.get("text", "") for b in resp.json().get("content", []))
        return parse_json(text)


def parse_json(text: str) -> dict:
    """Models sometimes wrap JSON in fences or add a sentence. Dig the object out."""
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise TransientError("LLM response had no JSON object")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise TransientError(f"LLM returned malformed JSON: {e}") from e


def make_llm(settings) -> LLM:
    if settings.anthropic_api_key:
        return AnthropicLLM(settings.anthropic_api_key, settings.model)
    return MockLLM(settings.sim_speed)

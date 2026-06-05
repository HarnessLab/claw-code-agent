"""
Forge — Kinetic Execution Layer.

Generates K candidate responses from the LLM using the IntentManifest's
temperature and k_candidates settings. Each candidate is independent —
different random seeds, same prompt.

The "Hermetic VFS" in the spec is just: candidates live in memory as
dataclasses. They are never written to disk until a winner is selected.
That's not a special feature — it's just how Python works. We name it
accurately here.

The "Sterile Prompt" is real: we strip social filler from the prompt
before sending to the model. "Please write a function that..." becomes
"Write a function that...". This reduces token waste and removes
sycophantic framing that can bias the model toward verbose explanations
over working code.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

from .intent_router import IntentManifest


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ForgeCandidate:
    """A single candidate response from the LLM."""
    candidate_id: int
    raw_text: str
    model: str
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int


# ---------------------------------------------------------------------------
# Sterile prompt
# ---------------------------------------------------------------------------

_FILLER_PATTERNS = [
    r'^(?:please\s+)?(?:can you\s+)?(?:could you\s+)?(?:would you\s+)?',
    r'^(?:i need you to\s+)',
    r'^(?:i want you to\s+)',
    r'^(?:i\'d like you to\s+)',
    r'(?:\s+please)$',
    r'(?:\s+thank you)$',
    r'(?:\s+thanks)$',
]


def sterilize(prompt: str) -> str:
    """
    Remove social filler from the prompt.
    Preserves all technical content.
    """
    result = prompt.strip()
    for pat in _FILLER_PATTERNS:
        result = re.sub(pat, '', result, flags=re.IGNORECASE).strip()
    # Capitalize first letter if we stripped the beginning
    if result and result[0].islower() and prompt[0].isupper():
        result = result[0].upper() + result[1:]
    return result


# ---------------------------------------------------------------------------
# Forge
# ---------------------------------------------------------------------------

class Forge:
    """
    Generates K candidates from the LLM.

    Uses the OpenAI-compatible client from the existing codebase.
    Each candidate is a separate API call with the same prompt but
    independent sampling (temperature > 0 means different outputs).
    """

    def __init__(self, client: Any, model: str):
        """
        client: an OpenAICompatClient instance (from openai_compat.py)
        model: model identifier string
        """
        self.client = client
        self.model = model

    def generate(
        self,
        prompt: str,
        manifest: IntentManifest,
        system_prompt: str = "",
        extra_context: str = "",
    ) -> list[ForgeCandidate]:
        """
        Generate K candidates synchronously.

        Returns a list of ForgeCandidate objects. May return fewer than K
        if some API calls fail — the Gauntlet handles empty candidates.
        """
        sterile = sterilize(prompt)
        k = manifest.k_candidates
        temperature = manifest.temperature

        # Build the full prompt with context
        full_prompt = sterile
        if extra_context:
            full_prompt = f"{extra_context}\n\n{sterile}"

        candidates: list[ForgeCandidate] = []

        for i in range(k):
            try:
                t0 = time.monotonic()
                response = self._call_model(
                    prompt=full_prompt,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    candidate_id=i,
                )
                latency_ms = (time.monotonic() - t0) * 1000

                if response:
                    candidates.append(ForgeCandidate(
                        candidate_id=i,
                        raw_text=response.get("content", ""),
                        model=self.model,
                        latency_ms=latency_ms,
                        prompt_tokens=response.get("prompt_tokens", 0),
                        completion_tokens=response.get("completion_tokens", 0),
                    ))
            except Exception as e:
                # Individual candidate failure doesn't kill the forge
                # The Gauntlet will handle the missing candidate
                pass

        return candidates

    def _call_model(
        self,
        prompt: str,
        system_prompt: str,
        temperature: float,
        candidate_id: int,
    ) -> Optional[dict[str, Any]]:
        """
        Make a single non-streaming call to the model.
        Returns dict with 'content', 'prompt_tokens', 'completion_tokens'.
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # Use the client's underlying HTTP call
        # The OpenAICompatClient in openai_compat.py handles auth/routing
        try:
            # Access the underlying requests session
            import json
            import urllib.request

            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": 2048,
                "stream": False,
            }

            # Use the client's base_url and api_key
            base_url = getattr(self.client, 'base_url', None) or \
                       getattr(self.client, '_base_url', None) or \
                       getattr(self.client, 'config', {}).get('base_url', '')
            api_key = getattr(self.client, 'api_key', None) or \
                      getattr(self.client, '_api_key', None) or \
                      getattr(self.client, 'config', {}).get('api_key', '')

            if not base_url:
                return None

            url = base_url.rstrip('/') + '/chat/completions'
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {api_key}',
                },
                method='POST',
            )

            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read().decode('utf-8'))

            content = body['choices'][0]['message']['content']
            usage = body.get('usage', {})
            return {
                'content': content,
                'prompt_tokens': usage.get('prompt_tokens', 0),
                'completion_tokens': usage.get('completion_tokens', 0),
            }

        except Exception:
            return None

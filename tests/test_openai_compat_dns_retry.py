"""Retry transient DNS failures in the OpenAI-compat client.

Live failure (2026-05-04 07:32):

  ❯ SAVE
  state-machine: llm_call - runtime_query_model
  checkpoint: d158f7afd554 typed-state saved
  LLM stream failed: OpenAICompatError('Unable to reach local model
  backend at https://openrouter.ai/api/v1: [Errno 8] nodename nor
  servname provided, or not known')

DNS recovered within the same minute (`nslookup openrouter.ai` →
104.18.2.115, `curl /v1/models` → 200). The error was a transient
blip the resolver recovered from. Pre-fix: every blip kills the turn
and surfaces a scary error. Post-fix: 1-2 retries with brief backoff
absorb transient DNS failures; real outages still surface.

Only `socket.gaierror` is retried — connection refused, timeout, and
HTTP errors must NOT auto-retry (those signal real problems and
masking them is worse than failing fast).
"""
from __future__ import annotations

import socket
import unittest
from urllib import error as urllib_error
from unittest.mock import MagicMock, patch

from src.openai_compat import OpenAICompatClient, OpenAICompatError
from src.agent_types import ModelConfig


def _config() -> ModelConfig:
    return ModelConfig(
        base_url='https://openrouter.ai/api/v1',
        api_key='test',
        model='claude-3.5-haiku',
        timeout_seconds=5,
    )


class _FakeResponse:
    """Minimal stand-in for a urllib response context manager."""
    def __init__(self, body: bytes) -> None:
        self._body = body
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self) -> bytes:
        return self._body


def _gaierror_url_error() -> urllib_error.URLError:
    return urllib_error.URLError(
        reason=socket.gaierror(8, 'nodename nor servname provided, or not known'),
    )


class TestDNSRetryOnTransientFailure(unittest.TestCase):
    def test_first_call_dns_fail_second_succeeds(self) -> None:
        client = OpenAICompatClient(_config())
        ok = _FakeResponse(b'{"choices":[{"message":{"content":"ok"},"finish_reason":"stop"}],"usage":{}}')
        urlopen_calls: list = []

        def fake_urlopen(req, timeout=None):
            urlopen_calls.append(req)
            if len(urlopen_calls) == 1:
                raise _gaierror_url_error()
            return ok

        with patch('src.openai_compat.request.urlopen', side_effect=fake_urlopen):
            payload = client._request_json({'messages': [], 'model': 'x'})

        self.assertEqual(len(urlopen_calls), 2, 'expected one retry after DNS failure')
        self.assertEqual(payload['choices'][0]['message']['content'], 'ok')

    def test_persistent_dns_failure_eventually_raises(self) -> None:
        client = OpenAICompatClient(_config())
        attempts: list = []

        def fake_urlopen(req, timeout=None):
            attempts.append(1)
            raise _gaierror_url_error()

        with patch('src.openai_compat.request.urlopen', side_effect=fake_urlopen):
            with self.assertRaises(OpenAICompatError) as ctx:
                client._request_json({'messages': [], 'model': 'x'})

        self.assertGreaterEqual(len(attempts), 2,
                                'should attempt at least once + retries before giving up')
        self.assertIn('Unable to reach', str(ctx.exception))

    def test_non_dns_url_error_does_not_retry(self) -> None:
        # Connection refused is a different signal — it means the
        # endpoint is reachable but rejecting; retrying is wrong.
        client = OpenAICompatClient(_config())
        attempts: list = []

        def fake_urlopen(req, timeout=None):
            attempts.append(1)
            raise urllib_error.URLError(reason=ConnectionRefusedError('refused'))

        with patch('src.openai_compat.request.urlopen', side_effect=fake_urlopen):
            with self.assertRaises(OpenAICompatError):
                client._request_json({'messages': [], 'model': 'x'})

        self.assertEqual(len(attempts), 1,
                         f'connection refused should NOT retry; got {len(attempts)} attempts')

    def test_http_error_does_not_retry(self) -> None:
        client = OpenAICompatClient(_config())
        attempts: list = []

        def fake_urlopen(req, timeout=None):
            attempts.append(1)
            raise urllib_error.HTTPError(
                url='https://x', code=400, msg='bad', hdrs=None, fp=None,
            )

        with patch('src.openai_compat.request.urlopen', side_effect=fake_urlopen):
            with self.assertRaises(OpenAICompatError):
                client._request_json({'messages': [], 'model': 'x'})

        self.assertEqual(len(attempts), 1, 'HTTP 400 must not retry')

    def test_streaming_path_also_retries_on_dns(self) -> None:
        # The streaming path uses the same _urlopen_with_dns_retry
        # helper, so verify the retry happens at the helper level
        # (which both call sites depend on).
        client = OpenAICompatClient(_config())
        urlopen_calls: list = []

        class _NoopResp:
            def __enter__(self): return self
            def __exit__(self, *_): return False

        def fake_urlopen(req, timeout=None):
            urlopen_calls.append(req)
            if len(urlopen_calls) == 1:
                raise _gaierror_url_error()
            return _NoopResp()

        from urllib import request as _req
        fake_request = _req.Request('https://example.invalid/x')
        with patch('src.openai_compat.request.urlopen', side_effect=fake_urlopen):
            client._urlopen_with_dns_retry(fake_request, timeout=5)

        self.assertEqual(len(urlopen_calls), 2,
                         f'helper must retry on DNS failure; got {len(urlopen_calls)}')


if __name__ == '__main__':
    unittest.main()

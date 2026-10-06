import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from src.inventory import fetch_discounted_models
from src.main import forward_to_openrouter
from src.config import settings


class TestP0Regressions(unittest.IsolatedAsyncioTestCase):

    async def test_fetch_discounted_models_concurrent(self):
        """Test concurrent endpoint fetching with semaphore."""
        models_payload = {
            "data": [
                {"id": "model-1"},
                {"id": "model-2"},
            ]
        }
        ep1_payload = {
            "data": {
                "endpoints": [
                    {
                        "provider": "prov1",
                        "pricing": {"discount": 0.6, "prompt": "0.001", "completion": "0.002"},
                        "context_length": 4096,
                    }
                ]
            }
        }
        ep2_payload = {
            "data": {
                "endpoints": [
                    {
                        "provider": "prov2",
                        "pricing": {"discount": 0.1, "prompt": "0.001", "completion": "0.002"},
                        "context_length": 4096,
                    }
                ]
            }
        }

        async def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.status_code = 200

            if url.endswith("/models"):
                resp.json.return_value = models_payload
            elif "/models/model-1/endpoints" in url:
                resp.json.return_value = ep1_payload
            elif "/models/model-2/endpoints" in url:
                resp.json.return_value = ep2_payload
            else:
                resp.status_code = 404
                resp.json.return_value = {}
            return resp

        mock_client = MagicMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(side_effect=mock_get)

        discounted = await fetch_discounted_models(client=mock_client)
        self.assertEqual(len(discounted), 1)
        self.assertEqual(discounted[0].id, "model-1")
        self.assertEqual(discounted[0].discount_pct, 60.0)

    async def test_forward_url_no_v1_duplication(self):
        """Regression test: Ensure openrouter base URL does not duplicate /v1."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}

        captured_url = None

        async def mock_post(url, **kwargs):
            nonlocal captured_url
            captured_url = url
            return mock_resp

        mock_client = MagicMock()
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch.object(settings, "use_litellm", False):
                with patch.object(settings, "inventory_url", "https://openrouter.ai/api/v1"):
                    with patch.object(settings, "openrouter_api_key", "sk-test"):
                        await forward_to_openrouter(
                            messages=[{"role": "user", "content": "hi"}],
                            model="model-1",
                            stream=False,
                        )
                        self.assertIn("/v1/chat/completions", captured_url)
                        self.assertNotIn("/v1/v1", captured_url)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with patch.object(settings, "use_litellm", True):
                with patch.object(settings, "litellm_proxy", "http://127.0.0.1:4000/"):
                    await forward_to_openrouter(
                        messages=[{"role": "user", "content": "hi"}],
                        model="model-1",
                        stream=False,
                    )
                    self.assertIn("/v1/chat/completions", captured_url)
                    self.assertNotIn("//v1", captured_url)

    async def test_forward_to_openrouter_streaming_connection_lifecycle(self):
        """Regression test: Streaming response stream must remain open during iteration."""
        fake_lines = ['data: {"choices": [{"delta": {"content": "hello"}}]}', 'data: [DONE]']

        async def async_line_iterator():
            for line in fake_lines:
                yield line

        mock_resp = type('MockResp', (), {
            'status_code': 200,
            'raise_for_status': MagicMock(),
            'aiter_lines': staticmethod(async_line_iterator),
            'aclose': AsyncMock(),
        })()

        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=mock_stream_ctx)
        mock_client.aclose = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            response = await forward_to_openrouter(
                messages=[{"role": "user", "content": "hi"}],
                model="model-1",
                stream=True,
            )

            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)

            self.assertEqual(len(chunks), 2)
            self.assertIn("hello", chunks[0])


if __name__ == "__main__":
    unittest.main()

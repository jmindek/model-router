import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from src.inventory import fetch_discounted_models


class TestScraperIntegration(unittest.IsolatedAsyncioTestCase):

    async def test_fetch_discounted_models_via_scraper(self):
        mock_scraped = {
            "inception/mercury-2.5": 80.0,
            "upstage/solar-pro4": 70.0,
            "low-discount/model": 20.0,  # Below default 50% threshold
        }

        mock_models_api_data = {
            "data": [
                {
                    "id": "inception/mercury-2.5",
                    "pricing": {"prompt": "0.00000004", "completion": "0.00000015"},
                    "context_length": 260000,
                    "top_provider": {"name": "Inception"},
                },
                {
                    "id": "upstage/solar-pro4",
                    "pricing": {"prompt": "0.00000009", "completion": "0.00000036"},
                    "context_length": 524288,
                    "top_provider": {"name": "Upstage"},
                },
            ]
        }

        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_file = Path(tmp_dir) / "models.json"

            with patch("src.inventory.scrape_discounted_models", new=AsyncMock(return_value=mock_scraped)), \
                 patch("src.inventory.settings.cache_file", str(cache_file)):

                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.raise_for_status = MagicMock()
                mock_resp.json.return_value = mock_models_api_data

                with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
                    models = await fetch_discounted_models()

                    # Should filter out 20% discount (below 50%) and keep 80% and 70%
                    self.assertEqual(len(models), 2)
                    self.assertEqual(models[0].id, "inception/mercury-2.5")
                    self.assertEqual(models[0].discount_pct, 80.0)
                    self.assertEqual(models[0].context_length, 260000)

                    self.assertEqual(models[1].id, "upstage/solar-pro4")
                    self.assertEqual(models[1].discount_pct, 70.0)

                    # Verify saved to cache
                    self.assertTrue(cache_file.exists())


if __name__ == "__main__":
    unittest.main()

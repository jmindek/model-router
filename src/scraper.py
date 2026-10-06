import asyncio
import logging
import re
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

MODELS_URL = "https://openrouter.ai/models?order=discount-high-to-low"


async def scrape_discounted_models() -> dict[str, float]:
    """Scrape discounted model IDs and discount percentages from OpenRouter models page.

    Navigates to /models?order=discount-high-to-low in a headless browser,
    captures all model cards with a discount badge ('XX% off') until
    non-discounted items appear.

    Returns:
        dict[str, float]: Map of model_id to discount_pct (e.g. {'inception/mercury-2.5': 80.0})
    """
    results: dict[str, float] = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
            )
            page = await context.new_page()
            logger.info(f"Navigating to {MODELS_URL}")
            await page.goto(MODELS_URL, wait_until="networkidle", timeout=30000)

            no_new_iterations = 0
            max_scroll_attempts = 15

            for _ in range(max_scroll_attempts):
                badges = page.locator("text=/\\d+%\\s*off/")
                badge_count = await badges.count()

                new_found = 0
                for i in range(badge_count):
                    badge = badges.nth(i)
                    badge_text = (await badge.text_content() or "").strip()
                    pct_match = re.search(r"(\d+)%", badge_text)
                    if not pct_match:
                        continue
                    pct = float(pct_match.group(1))

                    model_slug = await badge.evaluate("""el => {
                        let cur = el;
                        while (cur && cur !== document.body) {
                            const links = cur.querySelectorAll('a[href^="/"]');
                            for (const a of links) {
                                const href = a.getAttribute('href');
                                if (href && href.slice(1).split('/').length === 2 && !href.startsWith('/collection/')) {
                                    return href.slice(1);
                                }
                            }
                            cur = cur.parentElement;
                        }
                        return null;
                    }""")

                    if model_slug and model_slug not in results:
                        results[model_slug] = pct
                        new_found += 1

                # Check if the last card on the visible list lacks a discount badge.
                # If so, we've passed all discounted models.
                past_discounts = await page.evaluate("""() => {
                    const anchors = Array.from(document.querySelectorAll('a[href^="/"]'))
                        .filter(a => {
                            const h = a.getAttribute('href');
                            return h && h.slice(1).split('/').length === 2 && !h.startsWith('/collection/');
                        });
                    if (anchors.length === 0) return false;
                    const last = anchors[anchors.length - 1];
                    let cur = last;
                    while (cur && cur !== document.body) {
                        if (cur.textContent.includes('% off')) return false;
                        cur = cur.parentElement;
                    }
                    return true;
                }""")

                if past_discounts and len(results) > 0:
                    logger.info(f"Reached models without discount badges. Total found: {len(results)}")
                    break

                if new_found == 0:
                    no_new_iterations += 1
                    if no_new_iterations >= 3:
                        break
                else:
                    no_new_iterations = 0

                await page.evaluate("window.scrollBy(0, 1500)")
                await asyncio.sleep(0.5)

        finally:
            await browser.close()

    logger.info(f"Scraped {len(results)} discounted models from {MODELS_URL}")
    return results

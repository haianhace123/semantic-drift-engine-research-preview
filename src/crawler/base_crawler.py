"""
Base crawler with retry logic and fallback mechanisms.
"""

import time
import logging
from typing import Optional, Callable
from playwright.sync_api import sync_playwright
import requests

logger = logging.getLogger(__name__)


class BaseCrawler:
    """
    Base crawler with retry and fallback between requests and Playwright.
    """

    def __init__(self, max_retries: int = 3, timeout: int = 30):
        """
        Initialize base crawler.

        Args:
            max_retries: Maximum number of retry attempts.
            timeout: Request timeout in seconds.
        """
        self.max_retries = max_retries
        self.timeout = timeout
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        }

    def fetch_with_retry(self, url: str) -> Optional[str]:
        """
        Fetch HTML with retry logic and fallback.

        Args:
            url: Target URL.

        Returns:
            HTML string or None on failure.
        """
        for attempt in range(self.max_retries):
            try:
                # Try requests first
                logger.info(f"Attempt {attempt + 1}: Fetching with requests")
                response = requests.get(url, headers=self.headers, timeout=self.timeout)
                if response.status_code == 200:
                    return response.text

                logger.warning(f"Requests returned status {response.status_code}")

            except Exception as e:
                logger.warning(f"Requests failed: {e}")

            # Fallback to Playwright
            try:
                logger.info(f"Attempt {attempt + 1}: Fallback to Playwright")
                html = self._fetch_with_playwright(url)
                if html:
                    return html
            except Exception as e:
                logger.warning(f"Playwright failed: {e}")

            # Wait before retry
            if attempt < self.max_retries - 1:
                time.sleep(2 ** attempt)

        logger.error(f"All {self.max_retries} attempts failed for {url}")
        return None

    def _fetch_with_playwright(self, url: str) -> Optional[str]:
        """
        Fetch HTML using Playwright.

        Args:
            url: Target URL.

        Returns:
            HTML string or None on failure.
        """
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=self.timeout * 1000)
            html = page.content()
            browser.close()
            return html
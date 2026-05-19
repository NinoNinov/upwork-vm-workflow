"""
JobScraper — thin orchestration layer around upwork_analysis.scrape_data.JobsScraper.

Ported unchanged from the source UpWork Container project. The MySQL sink is
gone, but the scraper itself only produces per-title DataFrames — main.py is
where the storage decision is made.
"""
import concurrent.futures
import logging
import os
import random
import time
from typing import Dict, Optional

import pandas as pd
from seleniumbase import Driver
from tqdm import tqdm

from config import ScrapingConfig
from data_processor import JobDataProcessor
from upwork_analysis.scrape_data import JobsScraper
from utils import exponential_backoff

logger = logging.getLogger(__name__)


def _proxy_list() -> list[str]:
    """Parse UPWORK_PROXIES env var (comma-separated USER:PASS@HOST:PORT entries).

    `http://` and `https://` prefixes are stripped because seleniumbase's
    ``Driver(proxy=...)`` wants a bare auth string.
    """
    raw = os.environ.get("UPWORK_PROXIES", "")
    out = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "://" in entry:
            entry = entry.split("://", 1)[1]
        out.append(entry)
    return out


_PROXIES = _proxy_list()
if _PROXIES:
    # Monkey-patch JobsScraper.create_driver so every browser instance gets a
    # random proxy from UPWORK_PROXIES. Upstream upwork_analysis has no proxy
    # support, but seleniumbase's Driver() does -- we just wrap it.
    _orig_create_driver = JobsScraper.create_driver

    def _patched_create_driver(self):  # type: ignore[no-redef]
        proxy = random.choice(_PROXIES)
        # Log only host:port (strip credentials) so the proxy password never
        # appears in stdout / cron logs.
        host = proxy.split("@", 1)[-1]
        logger.info("Launching browser via proxy %s", host)
        return Driver(
            "chrome",
            headless=self.headless,
            uc=True,
            proxy=proxy,
            multi_proxy=True,  # required: each parallel worker uses its own proxy+auth
        )

    JobsScraper.create_driver = _patched_create_driver
    logger.info("Proxy support active -- %d proxies loaded.", len(_PROXIES))
else:
    logger.info("UPWORK_PROXIES not set -- driving Chrome without a proxy.")


class JobScraper:
    """Handle job scraping operations."""

    def __init__(self, config: ScrapingConfig) -> None:
        self.config = config
        self.processor = JobDataProcessor(config)

    def scrape_jobs_per_title_parallel(
        self, job_titles: Dict[str, int]
    ) -> Dict[str, pd.DataFrame]:
        """Scrape all job titles in parallel; return per-title DataFrames."""
        results: Dict[str, pd.DataFrame] = {}

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.config.max_workers
        ) as executor:
            future_to_title = {
                executor.submit(self._scrape_single_title, title, pages): title
                for title, pages in job_titles.items()
            }

            with tqdm(total=len(job_titles), desc="Scraping job titles") as pbar:
                for future in concurrent.futures.as_completed(future_to_title):
                    title = future_to_title[future]
                    try:
                        df = future.result(timeout=self.config.timeout)
                        if df is not None and not df.empty:
                            results[title] = df
                        else:
                            logger.warning("No data returned for '%s'", title)
                    except concurrent.futures.TimeoutError:
                        logger.error("Timeout scraping '%s'", title)
                    except Exception as exc:
                        logger.error("Error scraping '%s': %s", title, exc)
                    finally:
                        pbar.update(1)

        logger.info(
            "Parallel scrape complete: %d/%d titles succeeded",
            len(results), len(job_titles),
        )
        return results

    def scrape_jobs_by_titles(self, job_titles: Dict[str, int]) -> pd.DataFrame:
        """Backwards-compat helper returning a single combined DataFrame."""
        per_title = self.scrape_jobs_per_title_parallel(job_titles)
        if not per_title:
            logger.warning("No data collected from any job title.")
            return pd.DataFrame()
        combined = pd.concat(per_title.values(), ignore_index=True)
        logger.info("Combined DataFrame: %d rows across %d titles", len(combined), len(per_title))
        return combined

    def _scrape_single_title(
        self,
        title: str,
        pages_to_scrape: int,
        retry_count: int = 0,
    ) -> Optional[pd.DataFrame]:
        """Scrape jobs for a single title with exponential-backoff retry."""
        if retry_count > self.config.max_retries:
            logger.warning("Max retries (%d) exceeded for '%s'. Giving up.",
                           self.config.max_retries, title)
            return None

        try:
            logger.info(
                "Scraping '%s' -- %d page(s), attempt %d/%d",
                title, pages_to_scrape, retry_count + 1, self.config.max_retries + 1,
            )
            scraper = JobsScraper(
                search_query=title,
                jobs_per_page=self.config.jobs_per_page,
                start_page=self.config.start_page,
                pages_to_scrape=pages_to_scrape,
                retries=self.config.max_retries,
                headless=self.config.headless,
                workers=self.config.scraper_workers,
                fast=self.config.fast,
            )
            data = scraper.scrape_jobs()
            processed = self.processor.process_scraped_data(data, title)

            if processed is not None and not processed.empty:
                logger.info("Scraped %d jobs for '%s'", len(processed), title)
                return processed

            logger.warning("No data processed for '%s' -- retrying with fewer pages.", title)
            return self._retry_with_fewer_pages(title, pages_to_scrape, retry_count)

        except ValueError as exc:
            logger.warning("ValueError for '%s': %s -- retrying with fewer pages.", title, exc)
            return self._retry_with_fewer_pages(title, pages_to_scrape, retry_count)

        except Exception as exc:
            delay = exponential_backoff(retry_count, base_delay=2.0, max_delay=60.0)
            logger.error(
                "Error scraping '%s': %s. Retrying in %.1fs (attempt %d/%d).",
                title, exc, delay, retry_count + 1, self.config.max_retries,
            )
            time.sleep(delay)
            return self._scrape_single_title(title, pages_to_scrape, retry_count + 1)

    def _retry_with_fewer_pages(
        self,
        title: str,
        pages_to_scrape: int,
        retry_count: int,
    ) -> Optional[pd.DataFrame]:
        """Reduce pages by one and retry."""
        if pages_to_scrape > 1:
            return self._scrape_single_title(title, pages_to_scrape - 1, retry_count + 1)
        return None

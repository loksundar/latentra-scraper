"""
Fetches active job sources from the Hostinger PHP API.
All spiders use this instead of hardcoded company lists.
"""

import json
import logging
import os
import time

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

_SOURCES_URL = os.environ.get("SOURCES_URL", "")
_INGEST_TOKEN = os.environ.get("INGEST_TOKEN", "")

# Hostinger's bot filter 403s obvious bot UAs from datacenter IPs (GitHub
# runners) — a browser UA passes. The Authorization header still gates access.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_RETRIES = 6
_BACKOFF_SECONDS = 10  # exponential: 10, 20, 40, 80, 160s (~5 min horizon)

SOURCE_TYPES = ["greenhouse", "lever", "workday", "ashby", "smartrecruiters"]


def get_active_sources(source_type: str) -> list[dict]:
    """
    Return active sources for a source_type (greenhouse, lever, workday, ...)
    as a list of dicts with keys:
        source_type, company_slug, company_name, base_url

    If SOURCES_FILE is set, sources are read from that JSON file (written by
    fetch_sources.py). In CI this means the API is hit once per run instead of
    by five parallel jobs at the same instant, which was tripping the WAF.

    Otherwise fetches from the PHP API. Raises RuntimeError when the API can't
    be reached after retries — a crawl with silently-empty sources looks
    "green" while scraping nothing, which is worse than a loud failure.
    """
    sources_file = os.environ.get("SOURCES_FILE", "")
    if sources_file:
        with open(sources_file, encoding="utf-8") as f:
            data = json.load(f)
        sources = data.get(source_type, [])
        logger.info(f"Loaded {len(sources)} active {source_type} sources from {sources_file}")
        return sources

    if not _SOURCES_URL or not _INGEST_TOKEN:
        logger.warning("SOURCES_URL or INGEST_TOKEN not set — returning empty sources")
        return []

    last_error = None
    for attempt in range(1, _RETRIES + 1):
        try:
            resp = requests.get(
                _SOURCES_URL,
                params={"type": source_type},
                headers={
                    "Authorization": f"Bearer {_INGEST_TOKEN}",
                    "User-Agent": _UA,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            sources = data.get("sources", [])
            logger.info(f"Loaded {len(sources)} active {source_type} sources from API")
            return sources
        except Exception as e:
            last_error = e
            logger.warning(
                f"Sources fetch attempt {attempt}/{_RETRIES} failed for {source_type}: {e}"
            )
            if attempt < _RETRIES:
                time.sleep(_BACKOFF_SECONDS * (2 ** (attempt - 1)))

    raise RuntimeError(
        f"Failed to load sources for {source_type} after {_RETRIES} attempts: {last_error}"
    )

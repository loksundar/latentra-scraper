"""
Auto-discovery runner — fetches GitHub job lists, classifies URLs,
validates sources, and submits to the discovery API.

Reads source repos from discovery/github_sources.json.
Designed to run on a weekly schedule via Windows Task Scheduler.

Usage:
    python discover.py
    python discover.py --dry-run
    python discover.py --activate
"""

import argparse
import json
import logging
import os
import sys
import time

import requests
from dotenv import load_dotenv

# Add project root to path so imports work
sys.path.insert(0, os.path.dirname(__file__))

from latentra_scraper.discovery.classifier import classify_urls
from latentra_scraper.discovery.github_parser import extract_urls_from_markdown, fetch_github_raw
from latentra_scraper.discovery.validator import validate_source
from latentra_scraper.discovery.seeder import submit_to_api

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

SOURCES_FILE = os.path.join(os.path.dirname(__file__), "discovery", "github_sources.json")


def load_github_sources() -> list[dict]:
    """Load the list of GitHub repos to scan."""
    with open(SOURCES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def run_discovery(dry_run: bool = False, activate: bool = False):
    """Main discovery pipeline."""
    sources = load_github_sources()
    logger.info(f"Loaded {len(sources)} GitHub sources")

    all_urls = []

    # Step 1: Fetch and extract URLs from each GitHub source
    for src in sources:
        name = src["name"]
        url = src["url"]
        origin = src.get("origin", "github-list")

        try:
            logger.info(f"Fetching: {name} ({url})")
            text = fetch_github_raw(url)
            urls = extract_urls_from_markdown(text)
            logger.info(f"  Found {len(urls)} ATS URLs from {name}")
            all_urls.extend(urls)
        except Exception as e:
            logger.error(f"  Failed to fetch {name}: {e}")
            continue

    if not all_urls:
        logger.info("No URLs found across all sources")
        return

    logger.info(f"Total raw URLs: {len(all_urls)}")

    # Step 2: Classify all URLs (deduplicates internally)
    classified = classify_urls(all_urls)
    logger.info(f"Classified {len(classified)} unique sources")

    # Step 3: Validate each source
    validated = []
    for src in classified:
        try:
            result = validate_source(src)
            status = "OK" if result["valid"] else "FAIL"
            jobs = result.get("job_count", "?")
            logger.info(f"  [{status}] {src['source_type']:15} {src['company_slug']:20} jobs={jobs}")
            if result["valid"]:
                src["status"] = "validated"
                validated.append(src)
        except Exception as e:
            logger.error(f"  [ERR] {src['source_type']:15} {src['company_slug']:20} {e}")
        time.sleep(1)  # Rate limit

    logger.info(f"Validated: {len(validated)} / {len(classified)} sources")

    if dry_run:
        logger.info("Dry run — not submitting")
        for s in validated:
            logger.info(f"  Would submit: {s['source_type']:15} {s['company_slug']}")
        return

    if not validated:
        logger.info("No valid sources to submit")
        return

    # Step 4: Submit to discovery API
    try:
        result = submit_to_api(validated, origin="github-auto")
        inserted = result.get("inserted", 0)
        duplicates = result.get("duplicates", 0)
        logger.info(f"Submitted: {inserted} new, {duplicates} duplicates")
    except Exception as e:
        logger.error(f"Failed to submit to API: {e}")
        return

    # Step 5: Optionally auto-activate validated sources
    if activate and inserted > 0:
        logger.info("Auto-activation: promoting validated sources to job_sources...")
        discover_url = os.environ.get("DISCOVER_URL", "").rstrip("/") or (
            os.environ.get("INGEST_URL", "").replace("jobs-ingest.php", "jobs-discover.php")
        )
        token = os.environ.get("INGEST_TOKEN", "")
        if discover_url and token:
            try:
                resp = requests.put(
                    discover_url,
                    json={"status_filter": "validated", "action": "activate"},
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=30,
                )
                resp.raise_for_status()
                activate_result = resp.json()
                logger.info(f"Activated: {activate_result.get('activated', 0)} sources")
            except Exception as e:
                logger.error(f"Auto-activation failed: {e}")

    logger.info("Discovery complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Auto-discover companies from GitHub job lists")
    parser.add_argument("--dry-run", action="store_true", help="Classify and validate only, don't submit")
    parser.add_argument("--activate", action="store_true", help="Auto-activate validated sources")
    args = parser.parse_args()

    run_discovery(dry_run=args.dry_run, activate=args.activate)

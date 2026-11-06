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
from latentra_scraper.discovery.github_parser import extract_urls, fetch_github_raw
from latentra_scraper.discovery.validator import validate_source
from latentra_scraper.discovery.seeder import submit_to_api

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

SOURCES_FILE = os.path.join(os.path.dirname(__file__), "discovery", "github_sources.json")


def load_github_sources() -> list[dict]:
    """Load the list of GitHub repos to scan."""
    with open(SOURCES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# Per-run activation caps. Workday is the expensive spider (one detail request
# per job, large tenants), so new tenants are ramped in gradually rather than
# all at once — a sudden 5x jump would blow the crawl's time budget.
ACTIVATION_LIMITS = {"workday": 40, "greenhouse": 200, "ashby": 200, "lever": 200, "smartrecruiters": 50}

# Hard ceilings on ACTIVE sources per type, sized to the crawl matrix: each
# Workday shard handles ~60 tenants inside the 45-min watchdog, 6 shards in
# crawl.yml. Raise together with the shard count.
ACTIVE_CEILINGS = {"workday": 360}


def _activation_limits() -> dict:
    limits = dict(ACTIVATION_LIMITS)
    from latentra_scraper.sources.loader import get_active_sources
    for source_type, ceiling in ACTIVE_CEILINGS.items():
        try:
            current = len(get_active_sources(source_type))
        except Exception as e:  # can't count -> don't grow that type this run
            logger.warning(f"Could not count active {source_type} sources ({e}); skipping its activation")
            current = ceiling
        limits[source_type] = max(0, min(limits.get(source_type, 0), ceiling - current))
        logger.info(f"{source_type}: {current} active, ceiling {ceiling}, activating up to {limits[source_type]}")
    return limits

# Set when a step fails so the workflow exits non-zero (a red run emails the
# owner; before this, a 400 on activation logged an error but ran green).
_failures: list[str] = []


def activate_backlog() -> None:
    """Promote validated discovered_sources into job_sources, capped per type."""
    discover_url = os.environ.get("DISCOVER_URL", "").rstrip("/") or (
        os.environ.get("INGEST_URL", "").replace("jobs-ingest.php", "jobs-discover.php")
    )
    token = os.environ.get("INGEST_TOKEN", "")
    if not discover_url or not token:
        _failures.append("activation: DISCOVER_URL/INGEST_URL or INGEST_TOKEN not set")
        return
    logger.info("Auto-activation: promoting validated sources to job_sources...")
    try:
        resp = requests.put(
            discover_url,
            json={"status_filter": "validated", "action": "activate",
                  "limit_per_type": _activation_limits()},
            headers={"Authorization": f"Bearer {token}", "User-Agent": _UA},
            timeout=120,
        )
        resp.raise_for_status()
        result = resp.json()
        logger.info(
            f"Activated: {result.get('activated', 0)} sources "
            f"{result.get('by_type', {})}; still queued: {result.get('remaining', {})}"
        )
    except Exception as e:
        logger.error(f"Auto-activation failed: {e}")
        _failures.append(f"activation: {e}")


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
            urls = extract_urls(text, url)
            logger.info(f"  Found {len(urls)} ATS URLs from {name}")
            all_urls.extend(urls)
        except Exception as e:
            logger.error(f"  Failed to fetch {name}: {e}")
            _failures.append(f"list fetch {name}: {e}")
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
        time.sleep(0.4)  # Rate limit (most checks hit different hosts)

    logger.info(f"Validated: {len(validated)} / {len(classified)} sources")

    if dry_run:
        logger.info("Dry run — not submitting")
        for s in validated:
            logger.info(f"  Would submit: {s['source_type']:15} {s['company_slug']}")
        return

    if not validated:
        logger.info("No valid sources to submit")
        if activate:
            activate_backlog()
        return

    # Step 4: Submit to discovery API
    try:
        result = submit_to_api(validated, origin="github-auto")
        inserted = result.get("inserted", 0)
        duplicates = result.get("duplicates", 0)
        logger.info(f"Submitted: {inserted} new, {duplicates} duplicates")
    except Exception as e:
        logger.error(f"Failed to submit to API: {e}")
        _failures.append(f"submit: {e}")
        if activate:
            activate_backlog()
        return

    # Step 5: Optionally auto-activate validated sources. Runs even when
    # nothing new was inserted so the validated backlog keeps draining.
    if activate:
        activate_backlog()

    logger.info("Discovery complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Auto-discover companies from GitHub job lists")
    parser.add_argument("--dry-run", action="store_true", help="Classify and validate only, don't submit")
    parser.add_argument("--activate", action="store_true", help="Auto-activate validated sources")
    args = parser.parse_args()

    run_discovery(dry_run=args.dry_run, activate=args.activate)
    if _failures:
        for f in _failures:
            logger.error(f"FAILED STEP: {f}")
        sys.exit(1)

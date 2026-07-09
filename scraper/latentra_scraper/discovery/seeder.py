"""
Company seeder — takes URLs from various sources, classifies them,
validates they return jobs, and submits to the discovery API.

Usage:
    python -m latentra_scraper.discovery.seeder urls.txt
    python -m latentra_scraper.discovery.seeder urls.txt --validate --activate
"""

import argparse
import logging
import os
import sys
import time

import requests
from dotenv import load_dotenv

from latentra_scraper.discovery.classifier import classify_url, classify_urls
from latentra_scraper.discovery.validator import validate_source

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DISCOVER_URL = os.environ.get("DISCOVER_URL", "").rstrip("/") or (
    os.environ.get("INGEST_URL", "").replace("jobs-ingest.php", "jobs-discover.php")
)
INGEST_TOKEN = os.environ.get("INGEST_TOKEN", "")


def load_urls_from_file(filepath: str) -> list[str]:
    """Load URLs from a text file (one per line, skip comments and blanks)."""
    urls = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls


def submit_to_api(sources: list[dict], origin: str = "seeder") -> dict:
    """POST classified sources to the discovery endpoint."""
    if not DISCOVER_URL or not INGEST_TOKEN:
        logger.error("DISCOVER_URL or INGEST_TOKEN not set")
        return {}

    for s in sources:
        s["origin"] = origin

    resp = requests.post(
        DISCOVER_URL,
        json={"sources": sources},
        headers={"Authorization": f"Bearer {INGEST_TOKEN}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description="Seed companies from a URL list")
    parser.add_argument("file", help="Text file with one careers URL per line")
    parser.add_argument("--origin", default="manual", help="Origin tag (e.g. github-list, newgrad)")
    parser.add_argument("--validate", action="store_true", help="Check each source returns jobs before submitting")
    parser.add_argument("--dry-run", action="store_true", help="Classify and validate only, don't submit")
    args = parser.parse_args()

    urls = load_urls_from_file(args.file)
    logger.info(f"Loaded {len(urls)} URLs from {args.file}")

    classified = classify_urls(urls)
    unrecognized = len(urls) - len(classified)
    logger.info(f"Classified {len(classified)} sources ({unrecognized} unrecognized)")

    if args.validate:
        validated = []
        for src in classified:
            result = validate_source(src)
            status = "OK" if result["valid"] else "FAIL"
            jobs = result.get("job_count", "?")
            logger.info(f"  [{status}] {src['source_type']:15} {src['company_slug']:20} jobs={jobs}")
            if result["valid"]:
                src["company_name"] = result.get("company_name") or src.get("company_name")
                src["status"] = "validated"
                validated.append(src)
            time.sleep(1)
        classified = validated
        logger.info(f"Validated: {len(classified)} sources pass")

    if args.dry_run:
        logger.info("Dry run — not submitting")
        for s in classified:
            print(f"  {s['source_type']:15} {s['company_slug']:20} base_url={s.get('base_url') or '(standard)'}")
        return

    if not classified:
        logger.info("Nothing to submit")
        return

    result = submit_to_api(classified, origin=args.origin)
    logger.info(f"Submitted: {result.get('inserted', 0)} new, {result.get('duplicates', 0)} duplicates")


if __name__ == "__main__":
    main()

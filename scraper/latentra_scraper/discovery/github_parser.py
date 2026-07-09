"""
GitHub list parser — extracts company careers URLs from popular job list repos.
Supports markdown tables and lists commonly found in repos like:
  - SimplifyJobs/New-Grad-Positions
  - pittcsc/Summer2026-Internships
  - similar curated job lists

Usage:
    python -m latentra_scraper.discovery.github_parser <github_raw_url_or_file> [--output urls.txt]
"""

import argparse
import logging
import re
import sys

import requests

from latentra_scraper.discovery.classifier import classify_url

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Patterns to extract URLs from markdown
_URL_PATTERN = re.compile(r'https?://[^\s\)\]>"\']+')

# ATS domains we care about
ATS_DOMAINS = [
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "myworkdayjobs.com",
    "smartrecruiters.com",
    # Also catch direct careers pages that might redirect to ATS
    "boards.greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
]


def extract_urls_from_markdown(text: str) -> list[str]:
    """Extract all ATS-related URLs from markdown text."""
    urls = _URL_PATTERN.findall(text)
    # Filter to ATS-related domains
    ats_urls = []
    for url in urls:
        url = url.rstrip(".,;:!?)")
        for domain in ATS_DOMAINS:
            if domain in url.lower():
                ats_urls.append(url)
                break
    return ats_urls


def fetch_github_raw(url: str) -> str:
    """Fetch raw content from a GitHub URL (converts to raw if needed)."""
    # Convert github.com URL to raw.githubusercontent.com
    url = url.replace("github.com", "raw.githubusercontent.com")
    url = url.replace("/blob/", "/")

    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_source(source: str) -> str:
    """Read from file or fetch from URL."""
    if source.startswith("http"):
        logger.info(f"Fetching from URL: {source}")
        return fetch_github_raw(source)
    else:
        logger.info(f"Reading from file: {source}")
        with open(source, "r", encoding="utf-8") as f:
            return f.read()


def main():
    parser = argparse.ArgumentParser(description="Extract ATS URLs from GitHub job lists")
    parser.add_argument("source", help="GitHub URL or local markdown file")
    parser.add_argument("--output", "-o", default=None, help="Output file for extracted URLs")
    args = parser.parse_args()

    text = parse_source(args.source)
    urls = extract_urls_from_markdown(text)
    logger.info(f"Found {len(urls)} ATS-related URLs")

    # Deduplicate by classified identity
    seen = set()
    unique_urls = []
    for url in urls:
        result = classify_url(url)
        if result:
            key = (result["source_type"], result["company_slug"])
            if key not in seen:
                seen.add(key)
                unique_urls.append(url)
                logger.info(f"  {result['source_type']:15} {result['company_slug']}")

    logger.info(f"Unique companies: {len(unique_urls)}")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(f"# Extracted from {args.source}\n")
            for url in unique_urls:
                f.write(url + "\n")
        logger.info(f"Written to {args.output}")
    else:
        for url in unique_urls:
            print(url)


if __name__ == "__main__":
    main()

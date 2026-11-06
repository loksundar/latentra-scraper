"""Tests for discovery list parsing and the Workday listing prefilter."""

import json

from latentra_scraper.discovery.github_parser import extract_urls
from latentra_scraper.spiders.workday import WorkdaySpider


def test_listings_json_keeps_only_live_rows():
    rows = [
        {"url": "https://job-boards.greenhouse.io/acme/jobs/1", "active": True, "is_visible": True},
        {"url": "https://jobs.lever.co/dead/123", "active": False, "is_visible": True},
        {"url": "https://jobs.ashbyhq.com/hidden/x", "active": True, "is_visible": False},
        {"url": "https://example.com/careers", "active": True, "is_visible": True},
    ]
    urls = extract_urls(json.dumps(rows), "https://raw.githubusercontent.com/x/y/dev/listings.json")
    assert urls == ["https://job-boards.greenhouse.io/acme/jobs/1"]


def test_listings_json_garbage_is_empty():
    assert extract_urls("not json", "https://x/listings.json") == []


def test_markdown_still_parsed():
    md = "| Acme | [Apply](https://jobs.lever.co/acme/abc) |"
    assert extract_urls(md, "https://x/README.md") == ["https://jobs.lever.co/acme/abc"]


def test_workday_prefilter_skips_non_tech_and_non_us():
    spider = WorkdaySpider()
    assert spider._skip_from_listing({"title": "Registered Nurse - ICU", "locationsText": "Dallas, TX"})
    assert spider._skip_from_listing({"title": "Software Engineer", "locationsText": "Bangalore, India"})
    assert not spider._skip_from_listing({"title": "Senior Software Engineer", "locationsText": "Austin, TX"})
    assert not spider._skip_from_listing({"title": "Data Analyst", "locationsText": "3 Locations"})

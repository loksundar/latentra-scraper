"""
Golden-file regression tests for the ATS spiders.

Each fixture is a real-shaped API response. If an ATS changes its JSON format,
the spider silently starts emitting broken items — these tests pin the parsing
contract so that breakage shows up here first.

Run:  python -m pytest tests/ -v   (from scraper/)
"""

import json

import scrapy

from latentra_scraper.items import JobItem
from latentra_scraper.spiders.ashby import AshbySpider
from latentra_scraper.spiders.greenhouse import GreenhouseSpider
from latentra_scraper.spiders.lever import LeverSpider
from latentra_scraper.spiders.smartrecruiters import SmartRecruitersSpider
from latentra_scraper.spiders.workday import WorkdaySpider

from tests.conftest import fake_response, load_fixture


# ── Greenhouse ──────────────────────────────────────


class TestGreenhouse:
    URL = "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"

    def _items(self, source):
        spider = GreenhouseSpider()
        resp = fake_response(self.URL, load_fixture("greenhouse.json"))
        return list(spider.parse_jobs(resp, source))

    def test_yields_all_jobs(self, source):
        assert len(self._items(source)) == 2

    def test_field_mapping(self, source):
        job = self._items(source)[0]
        assert isinstance(job, JobItem)
        assert job["source"] == "greenhouse"
        assert job["source_job_id"] == "4011001"
        assert job["title"] == "Senior Backend Engineer"
        assert job["company"] == "Acme"
        assert job["company_slug"] == "acme"
        assert job["department"] == "Engineering"  # first department wins
        assert job["location"] == "New York, NY"
        assert job["apply_url"] == "https://boards.greenhouse.io/acme/jobs/4011001"
        assert job["posted_at"] == "2026-06-20 12:34:56"

    def test_location_as_plain_string(self, source):
        job = self._items(source)[1]
        assert job["location"] == "Remote - US"
        assert job["department"] is None

    def test_invalid_json_yields_nothing(self, source):
        spider = GreenhouseSpider()
        resp = fake_response(self.URL, "<html>Cloudflare interstitial</html>")
        assert list(spider.parse_jobs(resp, source)) == []


# ── Lever ───────────────────────────────────────────


class TestLever:
    URL = "https://api.lever.co/v0/postings/acme?mode=json"

    def _items(self, source):
        spider = LeverSpider()
        resp = fake_response(self.URL, load_fixture("lever.json"))
        return list(spider.parse_jobs(resp, source))

    def test_yields_all_jobs(self, source):
        assert len(self._items(source)) == 2

    def test_field_mapping(self, source):
        job = self._items(source)[0]
        assert job["source"] == "lever"
        assert job["source_job_id"] == "abc-123"
        assert job["title"] == "Staff Frontend Engineer"
        assert job["location"] == "London, UK"
        assert job["remote_type"] == "hybrid"
        assert job["employment_type"] == "full_time"
        assert job["department"] == "Web Platform"
        assert job["apply_url"] == "https://jobs.lever.co/acme/abc-123"
        assert job["posted_at"] == "2025-06-15 15:06:40"  # epoch ms 1750000000000 (UTC)

    def test_description_combines_lists_and_additional(self, source):
        desc = self._items(source)[0]["description_html"]
        assert "<h3>Requirements</h3>" in desc
        assert "<li>5+ years JavaScript</li>" in desc
        assert "equal opportunity" in desc

    def test_contract_and_remote_mapping(self, source):
        job = self._items(source)[1]
        assert job["employment_type"] == "contract"
        assert job["remote_type"] == "remote"

    def test_non_list_response_yields_nothing(self, source):
        spider = LeverSpider()
        resp = fake_response(self.URL, '{"error": "not found"}')
        assert list(spider.parse_jobs(resp, source)) == []


# ── Ashby ───────────────────────────────────────────


class TestAshby:
    URL = "https://api.ashbyhq.com/posting-api/job-board/acme"

    def _items(self, source):
        spider = AshbySpider()
        resp = fake_response(self.URL, load_fixture("ashby.json"))
        return list(spider.parse_jobs(resp, source))

    def test_unlisted_jobs_are_skipped(self, source):
        items = self._items(source)
        assert len(items) == 1
        assert items[0]["title"] == "Platform Engineer"

    def test_field_mapping(self, source):
        job = self._items(source)[0]
        assert job["source"] == "ashby"
        assert job["source_job_id"] == "a1b2c3"
        assert job["remote_type"] == "remote"        # isRemote=True wins
        assert job["employment_type"] == "full_time"
        assert job["department"] == "Infrastructure"
        assert job["apply_url"] == "https://jobs.ashbyhq.com/acme/a1b2c3/application"
        assert job["posted_at"] == "2026-06-01 00:00:00"


# ── Workday ─────────────────────────────────────────


BASE_URL = "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/careers"


class TestWorkday:
    def _source(self):
        return {
            "source_type": "workday",
            "company_slug": "acme",
            "company_name": "Acme",
            "base_url": BASE_URL,
        }

    def test_listing_yields_detail_requests_and_pagination(self):
        spider = WorkdaySpider()
        resp = fake_response(BASE_URL + "/jobs", load_fixture("workday_listing.json"))
        out = list(spider.parse_listing(resp, self._source(), offset=0))

        requests = [r for r in out if isinstance(r, scrapy.Request)]
        detail = [r for r in requests if r.method == "GET"]
        pages = [r for r in requests if r.method == "POST"]

        assert len(detail) == 2
        assert detail[0].url == BASE_URL + "/job/New-York/Data-Engineer_JR100"
        # total=22 > offset 0 + page size 20 → exactly one next-page request
        assert len(pages) == 1
        assert json.loads(pages[0].body)["offset"] == 20

    def test_detail_field_mapping(self):
        spider = WorkdaySpider()
        listing = {
            "title": "Data Engineer",
            "externalPath": "/en-US/careers/job/New-York/Data-Engineer_JR100",
            "locationsText": "New York",
            "bulletFields": ["JR100"],
        }
        resp = fake_response(
            BASE_URL + "/job/New-York/Data-Engineer_JR100",
            load_fixture("workday_detail.json"),
        )
        items = list(spider.parse_detail(resp, self._source(), listing))
        assert len(items) == 1
        job = items[0]
        assert job["source"] == "workday"
        assert job["source_job_id"] == "JR100"
        assert job["title"] == "Data Engineer"
        assert job["employment_type"] == "full_time"
        assert job["department"] == "Data & Analytics"
        # externalPath already contains /en-US/{site} — passed through verbatim
        assert job["apply_url"] == (
            "https://acme.wd5.myworkdayjobs.com/en-US/careers/job/New-York/Data-Engineer_JR100"
        )
        # "Posted 3 Days Ago" is relative, not a date — must map to None
        assert job["posted_at"] is None

    def test_apply_url_prefixes_site_when_missing(self):
        spider = WorkdaySpider()
        listing = {
            "title": "SRE",
            "externalPath": "/job/Austin/Site-Reliability-Engineer_JR200",
            "bulletFields": ["JR200"],
        }
        resp = fake_response(
            BASE_URL + "/job/Austin/Site-Reliability-Engineer_JR200",
            load_fixture("workday_detail.json"),
        )
        job = list(spider.parse_detail(resp, self._source(), listing))[0]
        assert job["apply_url"] == (
            "https://acme.wd5.myworkdayjobs.com/en-US/careers"
            "/job/Austin/Site-Reliability-Engineer_JR200"
        )


# ── SmartRecruiters ─────────────────────────────────


class TestSmartRecruiters:
    URL = "https://api.smartrecruiters.com/v1/companies/acme/postings?limit=100&offset=0"

    def _items(self, source):
        spider = SmartRecruitersSpider()
        resp = fake_response(self.URL, load_fixture("smartrecruiters.json"))
        return list(spider.parse_jobs(resp, source))

    def test_field_mapping(self, source):
        items = self._items(source)
        assert len(items) == 1
        job = items[0]
        assert job["source"] == "smartrecruiters"
        assert job["source_job_id"] == "744000012"
        assert job["title"] == "Security Analyst"
        assert job["location"] == "Austin, TX, us"
        assert job["remote_type"] == "remote"
        assert job["employment_type"] == "full_time"
        assert job["department"] == "Security"
        # ref link preferred over constructed URL
        assert job["apply_url"] == "https://jobs.smartrecruiters.com/Acme/744000012-security-analyst"
        assert job["posted_at"] == "2026-06-10 08:00:00"

    def test_description_combines_sections_with_titles(self, source):
        desc = self._items(source)[0]["description_html"]
        assert "<h3>Job Description</h3>" in desc
        assert "Monitor and triage alerts" in desc
        assert "<h3>Qualifications</h3>" in desc

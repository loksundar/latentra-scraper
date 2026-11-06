"""
Golden-file tests for the Workable, Rippling, Gem and Oracle spiders.
Fixtures are trimmed real API responses captured 2026-11-06.
"""

import scrapy

from latentra_scraper.discovery.classifier import classify_url
from latentra_scraper.items import JobItem
from latentra_scraper.spiders.gem import GemSpider
from latentra_scraper.spiders.oracle import OracleSpider, parse_base_url
from latentra_scraper.spiders.rippling import RipplingSpider
from latentra_scraper.spiders.workable import WorkableSpider

from tests.conftest import fake_response, load_fixture


# ── Workable ────────────────────────────────────────

class TestWorkable:
    URL = "https://apply.workable.com/api/v1/widget/accounts/acme?details=true"

    def _items(self, source):
        resp = fake_response(self.URL, load_fixture("workable.json"))
        return list(WorkableSpider().parse_jobs(resp, source))

    def test_field_mapping(self, source):
        items = self._items(source)
        assert len(items) == 2
        job = items[0]
        assert isinstance(job, JobItem)
        assert job["source"] == "workable"
        assert job["source_job_id"] == "AB12CD34EF"
        assert job["title"] == "Senior Software Engineer"
        assert job["company"] == "Acme"          # source name wins over widget name
        assert job["location"] == "Boston, Massachusetts, United States"
        assert job["employment_type"] == "full_time"
        assert job["posted_at"] == "2026-11-01 00:00:00"
        assert job["description_html"] == "<p>Build things.</p>"
        assert job["apply_url"] == "https://apply.workable.com/j/AB12CD34EF"

    def test_remote_and_fallback_url(self, source):
        job = self._items(source)[1]
        assert job["remote_type"] == "remote"
        assert job["location"] == "Remote"
        assert job["employment_type"] == "contract"
        assert job["apply_url"] == "https://apply.workable.com/acme/j/ZZ99/"

    def test_invalid_json(self, source):
        assert list(WorkableSpider().parse_jobs(fake_response(self.URL, "<html>"), source)) == []


# ── Rippling ────────────────────────────────────────

class TestRippling:
    LIST = "https://api.rippling.com/platform/api/ats/v1/board/acme/jobs"

    def test_listing_yields_detail_requests(self, source):
        reqs = list(RipplingSpider().parse_jobs(fake_response(self.LIST, load_fixture("rippling_list.json")), source))
        assert reqs and all(isinstance(r, scrapy.Request) for r in reqs)
        assert reqs[0].url.startswith(self.LIST + "/")

    def test_detail_mapping(self, source):
        listing = {"uuid": "x", "name": "Data Scientist I", "workLocation": {"label": "Chicago, IL"}}
        resp = fake_response(self.LIST + "/x", load_fixture("rippling_detail.json"))
        [job] = list(RipplingSpider().parse_detail(resp, source, listing))
        assert job["source"] == "rippling"
        assert job["title"] == "Data Scientist I"
        assert job["location"] == "Chicago, IL"
        assert job["employment_type"] == "full_time"
        assert (job["salary_min"], job["salary_max"], job["salary_currency"]) == (95500, 113500, "USD")
        assert job["description_html"] == "<p>Role</p><p>Co</p>"
        assert job["posted_at"] == "2026-01-19 07:59:15"
        assert job["apply_url"].startswith("https://ats.rippling.com/")


# ── Gem ─────────────────────────────────────────────

class TestGem:
    URL = "https://api.gem.com/job_board/v0/acme/job_posts/"

    def test_mapping(self, source):
        items = list(GemSpider().parse_jobs(fake_response(self.URL, load_fixture("gem.json")), source))
        assert len(items) == 2
        a, b = items
        assert a["source"] == "gem" and a["source_job_id"] == "g-1"
        assert a["location"] == "San Francisco, CA"
        assert a["department"] == "Engineering"
        assert a["employment_type"] == "full_time"
        assert a["posted_at"] == "2026-10-30 12:00:00"
        assert b["location"] == "Remote (US)" and b["remote_type"] == "remote"
        assert b["description_html"] == "x"


# ── Oracle ──────────────────────────────────────────

class TestOracle:
    BASE = "https://abcd.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1"

    def _source(self):
        return {"source_type": "oracle", "company_slug": "cx_1-abcd", "company_name": "Acme", "base_url": self.BASE}

    def test_parse_base_url(self):
        assert parse_base_url(self.BASE) == ("https://abcd.fa.ocs.oraclecloud.com", "CX_1")
        assert parse_base_url("https://example.com") == (None, None)

    def test_listing_prefilters_non_us_and_non_it(self):
        spider = OracleSpider()
        resp = fake_response("https://abcd.fa.ocs.oraclecloud.com/x", load_fixture("oracle_list.json"))
        reqs = list(spider.parse_listing(resp, self._source(), "https://abcd.fa.ocs.oraclecloud.com", "CX_1", 0))
        # Romania and Forklift rows skipped; only the US SWE row gets a detail fetch
        assert len(reqs) == 1
        assert 'Id=%221001%22' in reqs[0].url and "siteNumber=CX_1" in reqs[0].url

    def test_detail_mapping(self):
        resp = fake_response("https://abcd.fa.ocs.oraclecloud.com/d", load_fixture("oracle_detail.json"))
        listing = {"Id": "1001", "Title": "Software Engineer II", "PostedDate": "2026-11-02"}
        [job] = list(OracleSpider().parse_detail(resp, self._source(), "https://abcd.fa.ocs.oraclecloud.com", "CX_1", listing))
        assert job["source"] == "oracle"
        assert job["company"] == "Acme"
        assert job["location"] == "Phoenix, AZ, United States"
        assert job["remote_type"] == "hybrid"
        assert job["employment_type"] == "full_time"
        assert job["description_html"] == "<p>Desc</p><p>Quals</p>"
        assert job["posted_at"] == "2026-11-02 08:00:00"
        assert job["apply_url"] == self.BASE + "/job/1001"


# ── Discovery classification of the new ATSs ───────

def test_classifier_new_ats():
    assert classify_url("https://apply.workable.com/genetec-inc/j/145492924C/apply")["company_slug"] == "genetec-inc"
    assert classify_url("https://apply.workable.com/j/54A244302B") is None
    assert classify_url("https://ats.rippling.com/en-GB/acme/jobs")["source_type"] == "rippling"
    assert classify_url("https://jobs.gem.com/foo/am9i")["source_type"] == "gem"
    o = classify_url("https://ibqbjb.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/Honeywell/job/140055")
    assert o == {
        "source_type": "oracle",
        "company_slug": "honeywell-ibqbjb",
        "base_url": "https://ibqbjb.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/Honeywell",
    }

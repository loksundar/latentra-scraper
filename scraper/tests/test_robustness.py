"""
Defect regressions from the 2026-11-07 bug hunt.

Three families:
  1. Heartbeat false positives — a 2xx JSON body that is NOT the expected
     listing (error object, {} after an expired session, missing container
     key) must mark the company failed, never "checked cleanly with 0 jobs".
  2. Field-shape drift — None / str-for-dict swaps that crashed callbacks
     (which, besides losing the company, trip crawl.yml's exception guard).
  3. Filtering / parsing correctness — country-code based US filter, Workday
     posted_at + canonical apply URL, salary currency guards, partial-run
     flagging and heartbeat abort in the ingest pipeline.

Fixtures with a `_live`/`_drift`/`_intl` suffix are trimmed real API responses
captured 2026-11-07.
"""

import gzip
import json
import logging
from types import SimpleNamespace

import pytest
import scrapy
from scrapy.http import Request, TextResponse

import latentra_scraper.pipelines.ingest as ingest_mod
from latentra_scraper.enrichment.salary import extract_salary
from latentra_scraper.items import JobItem
from latentra_scraper.middlewares import CompanyReachMiddleware, mark_failed
from latentra_scraper.pipelines.ingest import IngestPipeline
from latentra_scraper.pipelines.validate import ValidatePipeline
from latentra_scraper.spiders.amazon import AmazonSpider
from latentra_scraper.spiders.apple import AppleSpider
from latentra_scraper.spiders.ashby import AshbySpider
from latentra_scraper.spiders.gem import GemSpider
from latentra_scraper.spiders.greenhouse import GreenhouseSpider
from latentra_scraper.spiders.lever import LeverSpider
from latentra_scraper.spiders.oracle import OracleSpider
from latentra_scraper.spiders.rippling import RipplingSpider
from latentra_scraper.spiders.smartrecruiters import SmartRecruitersSpider
from latentra_scraper.spiders.tiktok import TikTokSpider
from latentra_scraper.spiders.workable import WorkableSpider
from latentra_scraper.spiders.workday import WorkdaySpider

from tests.conftest import fake_response, load_fixture

WD_BASE = "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/careers"


def src(source_type="x", base_url=None):
    return {"source_type": source_type, "company_slug": "acme", "company_name": "Acme", "base_url": base_url}


def keyed_response(body, source, status=200, **cb_kwargs):
    """A response whose request carries `source` in cb_kwargs, like real crawls."""
    if not isinstance(body, str):
        body = json.dumps(body)
    req = Request("https://ats.example/acme", cb_kwargs={"source": source, **cb_kwargs})
    return TextResponse(req.url, status=status, body=body.encode(), encoding="utf-8", request=req)


# ── 1. Heartbeat false positives ─────────────────────────────────────────


ERROR_BODIES = [
    # spider, method name, extra kwargs, body that is JSON but not a listing
    (GreenhouseSpider, "parse_jobs", {}, {"error": "board not found"}),
    (GreenhouseSpider, "parse_jobs", {}, {"jobs": None}),
    (LeverSpider, "parse_jobs", {}, {"ok": False, "error": "Document not found"}),      # real Lever 404 body
    (AshbySpider, "parse_jobs", {}, {"errors": ["Unknown job board"]}),
    (AshbySpider, "parse_jobs", {}, {"jobs": None}),
    (WorkableSpider, "parse_jobs", {}, {"message": "Not Found"}),
    (GemSpider, "parse_jobs", {}, {"code": 404, "message": "Job board with vanity URL path x not found"}),  # real Gem body
    (RipplingSpider, "parse_jobs", {}, {"error_code": "RESOURCE_NOT_FOUND", "message": "Job Board not found"}),  # real
    (SmartRecruitersSpider, "parse", {"offset": 0}, {"message": "Company not found"}),
    (AmazonSpider, "parse_page", {"category": "software-development", "offset": 0}, {"error": "rate limited", "jobs": [], "hits": 0}),
    (AppleSpider, "parse_search", {"token": "t", "page": 1}, {}),                        # expired CSRF session
    (TikTokSpider, "parse_page", {"offset": 0}, {"code": 1001, "message": "invalid request", "data": None}),
    (OracleSpider, "parse_listing", {"host": "https://h", "site": "S", "offset": 0}, {"items": []}),
    (OracleSpider, "parse_listing", {"host": "https://h", "site": "S", "offset": 0}, {"title": "Unauthorized"}),
]


@pytest.mark.parametrize("spider_cls,method,kwargs,body", ERROR_BODIES,
                         ids=[f"{c.name}-{json.dumps(b)[:28]}" for c, _, _, b in ERROR_BODIES])
def test_error_json_marks_company_failed_not_reached(spider_cls, method, kwargs, body):
    spider = spider_cls()
    source = src(spider.source_type, base_url=WD_BASE)
    resp = keyed_response(body, source)
    mw = CompanyReachMiddleware()
    out = list(mw.process_spider_output(resp, getattr(spider, method)(resp, source=source, **kwargs), spider))
    assert out == []
    key = (spider.source_type, "acme")
    assert key in spider.companies_failed
    assert key in spider.companies_listing_failed
    # middleware still records "reached" (2xx JSON), but failed wins in the pipeline
    assert key not in (getattr(spider, "companies_reached", set()) - spider.companies_failed)


def test_workday_error_listing_marks_failed_and_empty_listing_is_clean():
    spider = WorkdaySpider()
    source = src("workday", base_url=WD_BASE)
    mw = CompanyReachMiddleware()
    bad = keyed_response({"message": "Site not found"}, source)
    assert list(mw.process_spider_output(bad, spider.parse_listing(bad, source=source, offset=0), spider)) == []
    assert ("workday", "acme") in spider.companies_failed

    clean = WorkdaySpider()
    ok = keyed_response({"total": 0, "jobPostings": []}, source)
    assert list(mw.process_spider_output(ok, clean.parse_listing(ok, source=source, offset=0), clean)) == []
    assert ("workday", "acme") in clean.companies_reached
    assert not getattr(clean, "companies_failed", set())


def test_workday_pagination_stops_when_a_page_comes_back_empty():
    spider = WorkdaySpider()
    source = src("workday", base_url=WD_BASE)
    resp = fake_response(WD_BASE + "/jobs", json.dumps({"total": 500, "jobPostings": []}))
    assert list(spider.parse_listing(resp, source, offset=20)) == []     # no runaway next-page request


def test_apple_missing_csrf_token_marks_failed_and_token_request_opts_out_of_json_sniff():
    spider = AppleSpider()
    source = src("apple")
    [req] = list(spider.build_requests(source))
    assert req.cb_kwargs.get("expect_json") is False
    # Real endpoint: 200 with an EMPTY body, token only in a header. Not a WAF page.
    mw = CompanyReachMiddleware()
    empty = TextResponse(req.url, status=200, body=b"", request=req)
    list(mw.process_spider_output(empty, spider.got_token(empty, source=source, expect_json=False), spider))
    assert ("apple", "acme") in spider.companies_failed          # no token → failed, explicitly
    # With a token: nothing failed, and the empty body is not held against Apple.
    ok_spider = AppleSpider()
    ok = TextResponse(req.url, status=200, body=b"", headers={"x-apple-csrf-token": "tok"}, request=req)
    out = list(mw.process_spider_output(ok, ok_spider.got_token(ok, source=source, expect_json=False), ok_spider))
    assert len(out) == 1 and isinstance(out[0], scrapy.Request)
    assert not getattr(ok_spider, "companies_failed", set())


def test_middleware_detail_failure_flags_company_incomplete():
    """Round 2: a lost detail means the listed job is missing from the batch,
    and the server would sweep it as stale — so it flags the run incomplete
    (partial=1) exactly like a lost listing page."""
    spider = SimpleNamespace(source_type="workday", logger=logging.getLogger("t"))
    source = src("workday")
    detail = Request("https://x/job/1", cb_kwargs={"source": source, "listing": {"title": "x"}})
    mark_failed(spider, detail)
    assert spider.companies_failed == {("workday", "acme")}
    assert spider.companies_listing_failed == {("workday", "acme")}
    page2 = Request("https://x/jobs", cb_kwargs={"source": source, "offset": 20})
    mark_failed(spider, page2)
    assert spider.companies_listing_failed == {("workday", "acme")}


# ── 2. Field-shape drift ─────────────────────────────────────────────────


def test_lever_survives_none_fields_and_filters_by_country_code(source):
    items = list(LeverSpider().parse_jobs(fake_response("https://api.lever.co/v0/postings/acme?mode=json",
                                                        load_fixture("lever_drift.json")), source))
    titles = [i["title"] for i in items]
    assert "Data Engineer (drifted row)" in titles              # categories/lists/description = None
    assert len(items) == 2                                      # GB row (country="GB") prefiltered
    drifted = next(i for i in items if i["title"].startswith("Data Engineer"))
    assert drifted["location"] == "" and drifted["description_html"] == ""
    assert drifted["employment_type"] == "unknown" and drifted["department"] is None
    us = next(i for i in items if not i["title"].startswith("Data Engineer"))
    assert "<h3>" in us["description_html"] and us["apply_url"].startswith("https://jobs.lever.co/")


def test_ashby_drops_non_us_by_postal_address(source):
    items = list(AshbySpider().parse_jobs(fake_response("https://api.ashbyhq.com/posting-api/job-board/acme",
                                                        load_fixture("ashby_intl.json")), source))
    assert [i["source_job_id"] for i in items] == ["1625aeeb-f518-4f16-81cb-a42c25ef1014"]
    assert items[0]["location"] == "Austin, TX"


def test_workable_drops_rows_whose_every_country_is_non_us(source):
    items = list(WorkableSpider().parse_jobs(fake_response("https://apply.workable.com/api/v1/widget/accounts/acme?details=true",
                                                           load_fixture("workable_intl.json")), source))
    assert [i["source_job_id"] for i in items] == ["US12345"]   # "Athens, Attica, Greece" row gone
    assert items[0]["location"] == "New York, New York, United States"


def test_rippling_listing_tolerates_string_work_location(source):
    rows = [{"uuid": "u1", "name": "Software Engineer", "workLocation": "Austin, TX", "department": "Eng",
             "url": "https://ats.rippling.com/acme/jobs/u1"}]
    reqs = list(RipplingSpider().parse_jobs(fake_response("https://api.rippling.com/x", json.dumps(rows)), source))
    assert len(reqs) == 1 and reqs[0].cb_kwargs["listing"]["uuid"] == "u1"


def test_rippling_detail_intern_shape_and_drift(source):
    listing = {"uuid": "x", "name": "Full Stack Software Engineer Intern - Summer 2027", "workLocation": {"label": "San Francisco, CA"}}
    body = load_fixture("rippling_detail_intern.json")
    [job] = list(RipplingSpider().parse_detail(fake_response("https://api.rippling.com/x", body), source, listing))
    assert job["employment_type"] == "intern"                   # label "TEMP", id "Temporary / Intern"
    assert job["location"] == "San Francisco, CA; New York, NY; Seattle, WA"
    assert job["salary_min"] is None                            # MONTH ranges are not annual salaries
    assert job["department"] == "Eng Interns"
    # str-for-dict drift on the fields that crashed the callback
    d = json.loads(body)
    d["employmentType"] = "Full-time"
    d["payRangeDetails"] = "n/a"
    d["department"] = "Engineering"
    d["description"] = "<p>plain</p>"
    [job] = list(RipplingSpider().parse_detail(fake_response("https://api.rippling.com/x", json.dumps(d)), source, listing))
    assert job["employment_type"] == "full_time" and job["department"] == "Engineering"
    assert job["description_html"] == "<p>plain</p>" and job["salary_min"] is None


def test_apple_row_tolerates_string_team_and_locations():
    spider = AppleSpider()
    row = {"id": "1", "positionId": "1", "postingTitle": "Software Engineer", "jobSummary": "x",
           "team": "Software and Services", "locations": "Cupertino", "postDateInGMT": None, "standardWeeklyHours": 40}
    item = spider.row_to_item(row, src("apple"))
    assert isinstance(item, JobItem) and item["location"] == "United States" and item["posted_at"] is None
    assert item["department"] == "Software and Services"


def test_tiktok_row_tolerates_string_category_and_recruit_type():
    row = {"id": "9", "title": "Backend Engineer", "description": "d", "requirement": None,
           "job_category": "R&D", "recruit_type": "Regular",
           "city_info": {"en_name": "Seattle", "parent": {"en_name": "Washington", "parent": {"en_name": "United States"}}}}
    [item] = list(TikTokSpider().parse_posts([row], src("tiktok")))
    assert item["department"] == "R&D" and item["employment_type"] == "full_time"
    assert item["location"] == "Seattle, Washington, US"


def test_greenhouse_skips_junk_rows_and_departments(source):
    body = {"jobs": [{"id": 1, "title": "SWE", "location": None, "departments": [None, {"name": "Eng"}], "content": None,
                      "absolute_url": None, "updated_at": None}, "junk", None]}
    [job] = list(GreenhouseSpider().parse_jobs(fake_response("https://boards-api.greenhouse.io/x", json.dumps(body)), source))
    assert job["department"] == "Eng" and job["location"] == "" and job["description_html"] == "" and job["apply_url"] == ""


# ── 3. Correctness ───────────────────────────────────────────────────────


class TestWorkdayDetailLive:
    """Real Adobe detail (2026-11-07): postedOn is relative, startDate is the
    real date, externalUrl is the canonical careers link, and the requisition
    location carries an ISO country code."""

    def _source(self):
        return src("workday", base_url="https://adobe.wd5.myworkdayjobs.com/wday/cxs/adobe/external_experienced")

    def _listing(self):
        return {"title": "Director Cybersecurity and Fraud Public Policy",
                "externalPath": "/job/Washington-DC/Director-Cybersecurity-and-Fraud-Public-Policy_R173038",
                "locationsText": "2 Locations", "postedOn": "Posted Yesterday", "bulletFields": ["R173038"]}

    def test_posted_at_from_start_date_and_external_url(self):
        resp = fake_response("https://adobe.wd5.myworkdayjobs.com/x", load_fixture("workday_detail_live.json"))
        [job] = list(WorkdaySpider().parse_detail(resp, self._source(), self._listing()))
        assert job["posted_at"] == "2026-11-06 00:00:00"
        assert job["apply_url"].startswith("https://adobe.wd5.myworkdayjobs.com/external_experienced/job/Washington")
        assert job["source_job_id"] == "R173038" and job["location"] == "Washington D.C."

    def test_non_us_requisition_country_is_dropped(self):
        resp = fake_response("https://adobe.wd5.myworkdayjobs.com/x", load_fixture("workday_detail_uk.json"))
        assert list(WorkdaySpider().parse_detail(resp, self._source(), self._listing())) == []

    def test_old_fixture_without_start_date_still_maps_none(self):
        resp = fake_response(WD_BASE + "/job/x", load_fixture("workday_detail.json"))
        [job] = list(WorkdaySpider().parse_detail(resp, src("workday", base_url=WD_BASE), self._listing()))
        assert job["posted_at"] is None


def test_smartrecruiters_prefers_full_location(source):
    spider = SmartRecruitersSpider()
    posting = {"id": "1", "name": "Data Engineer", "jobAd": {"sections": {"jobDescription": {"title": "Job", "text": "<p>x</p>"}}},
               "location": {"city": "San Jose", "region": "CA", "country": "us", "fullLocation": "San Jose, CA, United States"}}
    [item] = list(spider._parse_postings([posting], source))
    assert item["location"] == "San Jose, CA, United States"
    posting["location"].pop("fullLocation")
    [item] = list(spider._parse_postings([posting], source))
    assert item["location"] == "San Jose, CA, US"


def test_old_listing_rows_do_not_get_detail_requests(source):
    """Live 2026-11-07: a SmartRecruiters tenant with 1,342 postings from 2017
    cost 767 detail requests that the validator then dropped as too old."""
    sr = SmartRecruitersSpider()
    rows = [{"id": "old", "name": "Software Engineer", "releasedDate": "2017-09-12T21:27:42.000Z",
             "location": {"city": "San Jose", "region": "CA", "country": "us"}},
            {"id": "new", "name": "Software Engineer", "releasedDate": "2099-01-01T00:00:00.000Z",
             "location": {"city": "San Jose", "region": "CA", "country": "us"}},
            {"id": "nodate", "name": "Software Engineer", "location": {"city": "Austin", "region": "TX", "country": "us"}}]
    reqs = [r for r in sr._handle_postings(rows, source) if isinstance(r, scrapy.Request)]
    assert [r.cb_kwargs["listing"]["id"] for r in reqs] == ["new", "nodate"]

    # Oracle: listing is newest-first, so a page made only of old rows ends pagination
    oracle = OracleSpider()
    osrc = src("oracle", base_url="https://abcd.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1")
    page = {"items": [{"TotalJobsCount": 5000, "requisitionList": [
        {"Id": "1", "Title": "Software Engineer", "PrimaryLocation": "Phoenix, AZ, United States",
         "PrimaryLocationCountry": "US", "PostedDate": "2017-01-01"}]}]}
    out = list(oracle.parse_listing(fake_response("https://abcd.fa.ocs.oraclecloud.com/x", json.dumps(page)),
                                    osrc, "https://abcd.fa.ocs.oraclecloud.com", "CX_1", 0))
    assert out == []
    page["items"][0]["requisitionList"][0]["PostedDate"] = "2099-01-01"
    out = list(oracle.parse_listing(fake_response("https://abcd.fa.ocs.oraclecloud.com/x", json.dumps(page)),
                                    osrc, "https://abcd.fa.ocs.oraclecloud.com", "CX_1", 0))
    assert len(out) == 2                                    # detail request + next page


def test_validate_drops_canadian_province_codes():
    v = ValidatePipeline()
    assert not v._is_likely_us({"location": "Markham, ON"})
    assert not v._is_likely_us({"location": "Vancouver, BC, Canada"})
    assert not v._is_likely_us({"location": "Remote - Toronto, ON"})
    assert v._is_likely_us({"location": "Portland, OR"})
    assert v._is_likely_us({"location": "Washington, DC"})
    assert v._is_likely_us({"location": "Toronto, ON; New York, NY"})     # any US signal still wins


def test_salary_rejects_foreign_currency_and_non_annual_rates():
    assert extract_salary("Base pay CA$100,000 - CA$150,000 per year") is None
    assert extract_salary("Base pay C$100,000 - C$150,000") is None
    assert extract_salary("Salary range: $80,000 - $95,000 CAD") is None
    assert extract_salary("Salary range: $80,000 - $95,000 per month") is None
    assert extract_salary("Compensation: US$120,000 - US$160,000") == (120000, 160000)
    assert extract_salary("USD$120,000 - USD$160,000 annually") == (120000, 160000)
    assert extract_salary("Zone A $120,000 - $160,000 USD per year") == (120000, 160000)
    # a foreign range followed by the real US one: the US one is still found
    assert extract_salary("Canada: $90,000 - $110,000 CAD. United States: $120,000 - $150,000 USD") == (120000, 150000)


# ── Ingest pipeline: partial flag + heartbeat abort ──────────────────────


def _ingest_pipeline(monkeypatch, statuses):
    sent = []
    seq = list(statuses)

    def fake_post(url, data=None, headers=None, timeout=None):
        body = gzip.decompress(data) if headers.get("Content-Encoding") == "gzip" else data
        sent.append(json.loads(body))
        status = seq.pop(0) if seq else 200
        return SimpleNamespace(status_code=status, text="x",
                               json=lambda: {"inserted": 0, "updated": 0, "deleted": 0})

    monkeypatch.setattr(ingest_mod.req, "post", fake_post)
    monkeypatch.setattr(ingest_mod.time, "sleep", lambda s: None)
    p = IngestPipeline()
    p.enabled, p.ingest_url, p.ingest_token, p.start_time = True, "https://x/ingest", "t", ingest_mod.time.time()
    monkeypatch.setattr(p, "_validate_batch", lambda jobs, spider, slug: (jobs, []))
    return p, sent


def _spider():
    return SimpleNamespace(source_type="workday", logger=logging.getLogger("hb"), crawler=None,
                           validation_failures=0, validation_warnings=0)


def test_listing_failure_sends_company_batch_as_partial(monkeypatch):
    p, sent = _ingest_pipeline(monkeypatch, [])
    p.batches = {("workday", "page2died"): [{"source_job_id": "1", "apply_url": ""}],
                 ("workday", "detaildied"): [{"source_job_id": "2", "apply_url": ""}],
                 ("workday", "clean"): [{"source_job_id": "3", "apply_url": ""}]}
    s = _spider()
    s.companies_failed = {("workday", "page2died"), ("workday", "detaildied")}
    # mark_failed() puts both kinds of failure here (round 2: a lost detail is
    # an incomplete batch too — the server would sweep the listed job)
    s.companies_listing_failed = {("workday", "page2died"), ("workday", "detaildied")}
    p.close_spider(s)
    by_slug = {b["company_slug"]: b for b in sent}
    assert by_slug["page2died"].get("partial") == 1           # no stale sweep on an incomplete listing
    assert by_slug["detaildied"].get("partial") == 1          # nor on a lost detail
    assert "partial" not in by_slug["clean"]
    assert not [b for b in sent if b.get("heartbeat")]        # nothing to heartbeat here


def test_heartbeats_abort_after_consecutive_failures(monkeypatch):
    # every heartbeat POST fails (2 attempts each) → stop after 3 companies
    p, sent = _ingest_pipeline(monkeypatch, [503] * 100)
    s = _spider()
    s.companies_reached = {("workday", f"c{i}") for i in range(10)}
    s.companies_failed = set()
    p.close_spider(s)
    hb_slugs = {b["company_slug"] for b in sent if b.get("heartbeat")}
    assert len(hb_slugs) == ingest_mod.HEARTBEAT_ABORT_AFTER
    assert len(sent) == ingest_mod.HEARTBEAT_ABORT_AFTER * ingest_mod.HEARTBEAT_RETRIES


# ── Round 5: live spot-crawl findings ────────────────────────────────────────

def test_normalize_collapses_title_whitespace_and_private_use_glyphs():
    from latentra_scraper.pipelines.normalize import NormalizePipeline
    # Live Amazon title with an embedded newline
    item = JobItem(title="Senior Product Manager - Tech, Private Pricing Programs & Experiences \n, 3PX",
                   description_html="<p>x</p>")
    NormalizePipeline().process_item(item, None)
    assert item["title"] == "Senior Product Manager - Tech, Private Pricing Programs & Experiences , 3PX"
    # Live Apple title prefixed with the U+F8FF logo glyph
    item = JobItem(title=" Speech Processing ML Algorithm Engineer", description_html="<p>x</p>")
    NormalizePipeline().process_item(item, None)
    assert item["title"] == "Speech Processing ML Algorithm Engineer"
    # Nothing printable left: keep the original rather than send "" to ingest
    item = JobItem(title="", description_html="<p>x</p>")
    NormalizePipeline().process_item(item, None)
    assert item["title"] == ""
    # Ordinary titles are untouched
    item = JobItem(title="Staff Software Engineer, Payments", description_html="<p>x</p>")
    NormalizePipeline().process_item(item, None)
    assert item["title"] == "Staff Software Engineer, Payments"


@pytest.mark.parametrize("raw,state,city", [
    ("Remote Washington", "WA", None),      # live Adobe/Workday row: had no state or country
    ("Remote California", "CA", None),
    ("Remote in Texas", "TX", None),
    ("Hybrid New York", "NY", None),
    ("Remote Seattle", "WA", "Seattle"),
])
def test_mode_word_glued_to_a_place_is_a_us_location(raw, state, city):
    from latentra_scraper.enrichment.location import normalize_location
    r = normalize_location(raw)
    assert (r["location_state"], r["location_city"]) == (state, city)
    assert r["location_country"] == "United States"
    assert r["is_remote_friendly"] is (raw.lower().startswith("remote"))


def test_mode_word_without_a_place_stays_unknown():
    from latentra_scraper.enrichment.location import normalize_location
    for raw in ("Remote", "Remote or Hybrid", "Remote - US", "Anywhere"):
        r = normalize_location(raw)
        assert r["location_state"] is None and r["location_city"] is None, raw
    r = normalize_location("Remote in Canada")
    assert r["location_state"] is None and r["location_country"] == "Canada"
    assert normalize_location("Remote - US")["location_country"] == "United States"

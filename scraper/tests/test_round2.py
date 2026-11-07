"""
Round-2 regressions (2026-11-07 adversarial review of round 1).

Every case here is a way an INCOMPLETE batch could have reached the server
without partial=1 (so its stale sweep deleted live postings), or a way a real
US posting was dropped:

  1. a lost per-job detail (errback, non-JSON, empty body) left the job out of
     the batch while the batch still swept — now flags the company incomplete
  2. spiders that stop at their own paging cap with rows left sent a "full"
     batch — now mark the company incomplete
  3. the ingest pipeline only knew about the CLOSESPIDER_TIMEOUT watchdog;
     SIGTERM/SIGINT/cancel before it (Scrapy drops every queued request) sent
     full batches — now detected through the spider_idle signal
  4. Ashby postings with a foreign primary address but a US secondary location
     were dropped by round 1's country prefilter
  5. Oracle's all-old early stop trusted sortBy; an unsorted page could end
     paging early
"""

import json
import logging
from types import SimpleNamespace

import pytest
import scrapy
from scrapy.http import Request, TextResponse

import latentra_scraper.pipelines.ingest as ingest_mod
from latentra_scraper.middlewares import CompanyReachMiddleware, mark_failed, mark_incomplete
from latentra_scraper.pipelines.ingest import IngestPipeline
from latentra_scraper.pipelines.validate import ValidatePipeline
from latentra_scraper.spiders.amazon import AmazonSpider
from latentra_scraper.spiders.apple import AppleSpider
from latentra_scraper.spiders.ashby import AshbySpider
from latentra_scraper.spiders.oracle import OracleSpider
from latentra_scraper.spiders.rippling import RipplingSpider
from latentra_scraper.spiders.smartrecruiters import SmartRecruitersSpider
from latentra_scraper.spiders.tiktok import TikTokSpider
from latentra_scraper.spiders.workday import WorkdaySpider

from tests.conftest import fake_response, load_fixture
from tests.test_robustness import _ingest_pipeline, keyed_response, src

WD_BASE = "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/careers"
ORA_HOST = "https://abcd.fa.ocs.oraclecloud.com"


# ── 1. Detail failures flag the company incomplete ───────────────────────


DETAIL_FAILS = [
    (WorkdaySpider, {}, {"listing": {"title": "SWE", "externalPath": "/job/x/SWE_R1"}}),
    (WorkdaySpider, "<html>blocked</html>", {"listing": {"title": "SWE", "externalPath": "/job/x/SWE_R1"}}),
    (OracleSpider, {"items": []}, {"host": ORA_HOST, "site": "CX_1", "listing": {"Id": "1", "Title": "SWE"}}),
    (OracleSpider, {"title": "Unauthorized"}, {"host": ORA_HOST, "site": "CX_1", "listing": {"Id": "1", "Title": "SWE"}}),
    (RipplingSpider, {"error_code": "RESOURCE_NOT_FOUND", "message": "x"}, {"listing": {"uuid": "u1", "name": "SWE"}}),
    (RipplingSpider, [], {"listing": {"uuid": "u1", "name": "SWE"}}),
    (SmartRecruitersSpider, {"message": "Not found"}, {"listing": {"id": "1", "name": "SWE"}}),
    (SmartRecruitersSpider, "<html>429</html>", {"listing": {"id": "1", "name": "SWE"}}),
]


@pytest.mark.parametrize("spider_cls,body,kwargs", DETAIL_FAILS,
                         ids=[f"{c.name}-{json.dumps(b)[:20]}" for c, b, _ in DETAIL_FAILS])
def test_unusable_detail_marks_company_incomplete(spider_cls, body, kwargs):
    spider = spider_cls()
    source = src(spider.source_type, base_url=WD_BASE)
    resp = keyed_response(body, source, **kwargs)
    mw = CompanyReachMiddleware()
    out = list(mw.process_spider_output(resp, spider.parse_detail(resp, source=source, **kwargs), spider))
    assert out == []                                         # no half-built item either
    key = (spider.source_type, "acme")
    assert key in spider.companies_listing_failed            # → partial=1
    assert key in spider.companies_failed                    # → no heartbeat


def test_detail_errback_marks_company_incomplete():
    spider = SimpleNamespace(source_type="oracle", logger=logging.getLogger("t"))
    detail = Request("https://x/detail?Id=1", cb_kwargs={"source": src("oracle"), "listing": {"Id": "1"}})
    mark_failed(spider, detail)
    assert spider.companies_listing_failed == {("oracle", "acme")}


def test_good_detail_still_builds_items_and_leaves_company_clean(source):
    source = dict(source, source_type="workday", base_url=WD_BASE)
    spider = WorkdaySpider()
    listing = {"title": "Data Engineer", "externalPath": "/job/New-York/Data-Engineer_JR100", "bulletFields": ["JR100"]}
    resp = keyed_response(load_fixture("workday_detail_live.json"), source, listing=listing)
    mw = CompanyReachMiddleware()
    [job] = list(mw.process_spider_output(resp, spider.parse_detail(resp, source=source, listing=listing), spider))
    assert job["source_job_id"] == "R173038"
    assert not getattr(spider, "companies_listing_failed", set())


# ── 2. Paging caps flag the company incomplete ───────────────────────────


def test_smartrecruiters_paging_cap_marks_incomplete(monkeypatch):
    spider = SmartRecruitersSpider()
    monkeypatch.setattr(SmartRecruitersSpider, "MAX_PAGES", 1)
    source = src("smartrecruiters")
    rows = [{"id": str(i), "name": "Software Engineer", "releasedDate": "2099-01-01T00:00:00.000Z",
             "location": {"city": "Austin", "region": "TX", "country": "us"},
             "jobAd": {"sections": {"jobDescription": {"text": "<p>x</p>"}}}} for i in range(100)]
    resp = keyed_response({"content": rows, "totalFound": 250, "limit": 100, "offset": 0}, source, offset=0)
    out = list(spider.parse(resp, source=source, offset=0))
    assert len(out) == 100 and not any(isinstance(o, scrapy.Request) for o in out)     # no page 2
    assert ("smartrecruiters", "acme") in spider.companies_listing_failed
    assert ("smartrecruiters", "acme") not in getattr(spider, "companies_failed", set())  # not a failure

    # below the cap the next page is requested and nothing is flagged
    clean = SmartRecruitersSpider()
    monkeypatch.setattr(SmartRecruitersSpider, "MAX_PAGES", 30)
    out = list(clean.parse(resp, source=source, offset=0))
    assert sum(isinstance(o, scrapy.Request) for o in out) == 1
    assert not getattr(clean, "companies_listing_failed", set())


def test_oracle_paging_cap_marks_incomplete(monkeypatch):
    spider = OracleSpider()
    monkeypatch.setattr(OracleSpider, "MAX_PAGES", 1)
    source = src("oracle", base_url=f"{ORA_HOST}/hcmUI/CandidateExperience/en/sites/CX_1")
    rows = [{"Id": str(i), "Title": "Software Engineer", "PrimaryLocation": "Austin, TX, United States",
             "PrimaryLocationCountry": "US", "PostedDate": "2099-01-01"} for i in range(25)]
    resp = keyed_response({"items": [{"TotalJobsCount": 900, "requisitionList": rows}]}, source,
                          host=ORA_HOST, site="CX_1", offset=0)
    out = list(spider.parse_listing(resp, source=source, host=ORA_HOST, site="CX_1", offset=0))
    assert len(out) == 25 and all("recruitingCEJobRequisitionDetails" in r.url for r in out)   # details, no page 2
    assert ("oracle", "acme") in spider.companies_listing_failed


@pytest.mark.parametrize("spider_cls,attr,body,kwargs", [
    (AmazonSpider, "MAX_OFFSET", {"error": None, "hits": 5000,
                                  "jobs": [{"id_icims": str(i), "title": "SDE", "description": "x",
                                            "normalized_location": "Seattle, WA, USA", "job_path": "/en/jobs/1",
                                            "posted_date": "November 6, 2026"} for i in range(100)]},
     {"category": "software-development", "offset": 0}),
    (TikTokSpider, "MAX_OFFSET", {"code": 0, "data": {"count": 5000, "job_post_list": [
        {"id": str(i), "title": "Backend Engineer", "description": "d", "job_category": {"en_name": "R&D"},
         "city_info": {"en_name": "Seattle", "parent": {"en_name": "United States"}}} for i in range(100)]}},
     {"offset": 0}),
])
def test_single_company_paging_caps_mark_incomplete(spider_cls, attr, body, kwargs):
    spider = spider_cls()
    setattr(spider, attr, 100)                      # cap right after page 1
    source = src(spider.source_type)
    resp = keyed_response(body, source, **kwargs)
    out = list(spider.parse_page(resp, source=source, **kwargs))
    assert len(out) == 100 and not any(isinstance(o, scrapy.Request) for o in out)
    assert (spider.source_type, "acme") in spider.companies_listing_failed


def test_apple_paging_cap_marks_incomplete():
    spider = AppleSpider()
    spider.MAX_PAGES = 1
    source = src("apple")
    body = {"res": {"totalRecords": 4606, "searchResults": [
        {"id": str(i), "positionId": str(i), "postingTitle": "Software Engineer", "jobSummary": "x",
         "team": {"teamName": "Software and Services"}, "locations": [{"name": "Cupertino", "stateProvince": "CA"}],
         "postDateInGMT": "2026-11-07T11:21:06.257740844Z", "standardWeeklyHours": 40} for i in range(20)]}}
    resp = keyed_response(body, source, token="t", page=1)
    out = list(spider.parse_search(resp, source=source, token="t", page=1))
    assert len(out) == 20 and not any(isinstance(o, scrapy.Request) for o in out)
    assert ("apple", "acme") in spider.companies_listing_failed


def test_mark_incomplete_alone_does_not_block_heartbeat():
    spider = SimpleNamespace(source_type="amazon", logger=logging.getLogger("t"))
    mark_incomplete(spider, Request("https://x", cb_kwargs={"source": src("amazon")}))
    assert spider.companies_listing_failed == {("amazon", "acme")}
    assert not getattr(spider, "companies_failed", set())


# ── 3. Forced close (shutdown / cancel) → every batch partial ────────────


class _Signals:
    def __init__(self):
        self.handlers = {}

    def connect(self, receiver, signal):
        self.handlers[signal] = receiver


def _crawled_pipeline(monkeypatch):
    """IngestPipeline wired the way Scrapy wires it (from_crawler + signals)."""
    from scrapy import signals as scrapy_signals
    crawler = SimpleNamespace(signals=_Signals())
    sent = []

    def fake_post(url, data=None, headers=None, timeout=None):
        import gzip
        body = gzip.decompress(data) if headers.get("Content-Encoding") == "gzip" else data
        sent.append(json.loads(body))
        return SimpleNamespace(status_code=200, text="x", json=lambda: {"inserted": 0, "updated": 0, "deleted": 0})

    monkeypatch.setattr(ingest_mod.req, "post", fake_post)
    monkeypatch.setattr(ingest_mod.time, "sleep", lambda s: None)
    p = IngestPipeline.from_crawler(crawler)
    p.enabled, p.ingest_url, p.ingest_token, p.start_time = True, "https://x/ingest", "t", ingest_mod.time.time()
    monkeypatch.setattr(p, "_validate_batch", lambda jobs, spider, slug: (jobs, []))
    p.batches = {("workday", "clean"): [{"source_job_id": "1", "apply_url": ""}]}
    spider = SimpleNamespace(source_type="workday", logger=logging.getLogger("hb"), crawler=None,
                             validation_failures=0, validation_warnings=0,
                             companies_reached={("workday", "clean"), ("workday", "zero")}, companies_failed=set())
    return p, sent, spider, crawler.signals.handlers[scrapy_signals.spider_idle]


def test_close_without_spider_idle_sends_everything_partial(monkeypatch):
    p, sent, spider, _idle = _crawled_pipeline(monkeypatch)
    p.close_spider(spider)                                   # SIGTERM / cancel: idle never fired
    by_slug = {b["company_slug"]: b for b in sent}
    assert by_slug["clean"]["partial"] == 1
    assert by_slug["zero"]["heartbeat"] == 1 and by_slug["zero"]["partial"] == 1


def test_close_after_spider_idle_is_a_full_run(monkeypatch):
    p, sent, spider, idle = _crawled_pipeline(monkeypatch)
    idle(spider)                                             # natural "finished" close
    p.close_spider(spider)
    by_slug = {b["company_slug"]: b for b in sent}
    assert "partial" not in by_slug["clean"]
    assert "partial" not in by_slug["zero"]


def test_pipeline_without_crawler_keeps_runtime_watchdog_only(monkeypatch):
    p, sent = _ingest_pipeline(monkeypatch, [])
    assert p.saw_idle is None
    p.batches = {("workday", "clean"): [{"source_job_id": "1", "apply_url": ""}]}
    p.close_spider(SimpleNamespace(source_type="workday", logger=logging.getLogger("hb"), crawler=None,
                                   validation_failures=0, validation_warnings=0))
    assert "partial" not in sent[0]


# ── 4. Ashby: US secondary location keeps the posting ────────────────────


def test_ashby_keeps_posting_with_us_secondary_location(source):
    items = list(AshbySpider().parse_jobs(fake_response("https://api.ashbyhq.com/posting-api/job-board/acme",
                                                        load_fixture("ashby_multi_location.json")), source))
    by_id = {i["source_job_id"][-1]: i for i in items}
    assert set(by_id) == {"1", "3"}                          # London/Dublin-only row dropped
    assert by_id["1"]["location"] == "Remote (US)"           # foreign primary → first US secondary shown
    assert by_id["3"]["location"] == "New York, NY (HQ)"     # US primary untouched
    v = ValidatePipeline()
    assert all(v._is_likely_us({"location": i["location"]}) for i in items)   # validator keeps them


# ── 5. Oracle: all-old early stop only on a sorted page ──────────────────


def test_oracle_unsorted_all_old_page_keeps_paging():
    spider = OracleSpider()
    source = src("oracle", base_url=f"{ORA_HOST}/hcmUI/CandidateExperience/en/sites/CX_1")

    def page(dates):
        rows = [{"Id": str(i), "Title": "Software Engineer", "PrimaryLocation": "Austin, TX, United States",
                 "PrimaryLocationCountry": "US", "PostedDate": d} for i, d in enumerate(dates)]
        return fake_response(f"{ORA_HOST}/x", json.dumps({"items": [{"TotalJobsCount": 5000, "requisitionList": rows}]}))

    sorted_old = list(spider.parse_listing(page(["2017-03-01", "2017-02-01", "2017-01-01"]), source, ORA_HOST, "CX_1", 0))
    assert sorted_old == []                                               # trusted: newest-first and all old
    unsorted_old = list(spider.parse_listing(page(["2017-01-01", "2017-03-01", "2017-02-01"]), source, ORA_HOST, "CX_1", 0))
    assert len(unsorted_old) == 1 and "offset=25" in unsorted_old[0].url  # sort not honoured → next page

"""Zero-job companies that were checked cleanly get an empty heartbeat batch."""

import json
import gzip
import logging
from types import SimpleNamespace

from scrapy.http import Request, TextResponse

import latentra_scraper.pipelines.ingest as ingest_mod
from latentra_scraper.middlewares import CompanyReachMiddleware, mark_failed
from latentra_scraper.pipelines.ingest import IngestPipeline


def _spider():
    return SimpleNamespace(source_type="lever", logger=logging.getLogger("hb"),
                           crawler=None, validation_failures=0, validation_warnings=0)


def _resp(slug, body='{"jobs": []}', status=200):
    req = Request("https://api.example.com/" + slug, cb_kwargs={"source": {"company_slug": slug, "source_type": "lever"}})
    return TextResponse(req.url, status=status, body=body.encode(), encoding="utf-8", request=req)


def _run(mw, resp, spider, items=(), boom=False):
    def gen():
        yield from items
        if boom:
            raise ValueError("bad shape")
    out = mw.process_spider_output(resp, gen(), spider)
    try:
        return list(out)
    except ValueError:
        mw.process_spider_exception(resp, ValueError(), spider)
        return []


def test_middleware_marks_clean_json_and_disqualifies_failures():
    s, mw = _spider(), CompanyReachMiddleware()
    _run(mw, _resp("ok"), s)
    _run(mw, _resp("waf", body="<html>blocked</html>"), s)
    _run(mw, _resp("crash"), s, boom=True)
    _run(mw, _resp("mixed"), s)
    mark_failed(s, _resp("mixed").request)          # e.g. page 2 errback
    assert s.companies_reached == {("lever", "ok"), ("lever", "mixed")}
    assert {("lever", "waf"), ("lever", "crash"), ("lever", "mixed")} <= s.companies_failed


def test_close_spider_sends_heartbeat_only_for_clean_zero_job_companies(monkeypatch):
    sent = []

    def fake_post(url, data=None, headers=None, timeout=None):
        body = gzip.decompress(data) if headers.get("Content-Encoding") == "gzip" else data
        sent.append(json.loads(body))
        return SimpleNamespace(status_code=200, text="ok", json=lambda: {"inserted": 0, "updated": 0, "deleted": 0})

    monkeypatch.setattr(ingest_mod.req, "post", fake_post)
    monkeypatch.setattr(ingest_mod.time, "sleep", lambda s: None)
    p = IngestPipeline()
    p.enabled, p.ingest_url, p.ingest_token, p.start_time = True, "https://x/ingest", "t", ingest_mod.time.time()
    p.batches = {("lever", "hasjobs"): [{"source_job_id": "1", "apply_url": ""}]}
    monkeypatch.setattr(p, "_validate_batch", lambda jobs, spider, slug: (jobs, []))
    s = _spider()
    s.companies_reached = {("lever", "hasjobs"), ("lever", "empty"), ("lever", "broken")}
    s.companies_failed = {("lever", "broken")}
    p.close_spider(s)
    slugs = [b["company_slug"] for b in sent]
    assert slugs.count("hasjobs") == 1          # normal batch, no duplicate heartbeat
    hb = [b for b in sent if b.get("heartbeat")]
    assert [b["company_slug"] for b in hb] == ["empty"]
    assert hb[0]["jobs"] == []
    assert "broken" not in slugs

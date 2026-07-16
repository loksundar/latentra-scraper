"""Tests for the ingest pipeline's gzip, retry, and WAF-split behavior."""

import gzip
import json
import logging
from types import SimpleNamespace

import latentra_scraper.pipelines.ingest as ingest_mod
from latentra_scraper.pipelines.ingest import IngestPipeline


class FakeResp:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self.text = "Forbidden" if status_code != 200 else "ok"
        self._body = body if body is not None else {"inserted": 0, "updated": 0, "deleted": 0}

    def json(self):
        return self._body


def make_pipeline():
    p = IngestPipeline()
    p.ingest_url = "https://example.com/ingest"
    p.ingest_token = "tok"
    return p


def make_spider():
    return SimpleNamespace(logger=logging.getLogger("test-spider"))


def make_payload(n_jobs):
    return {
        "source": "ashby", "company_slug": "acme",
        "jobs": [{"source_job_id": str(i)} for i in range(n_jobs)],
        "dead_source_job_ids": ["d1"], "validation_failures": 1,
        "validation_warnings": 2, "runtime_seconds": 1,
    }


def patch_post(monkeypatch, responses):
    """req.post returns the given responses in order; records each call."""
    seq = list(responses)
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs)
        return seq.pop(0)

    monkeypatch.setattr(ingest_mod.req, "post", fake_post)
    monkeypatch.setattr(ingest_mod.time, "sleep", lambda s: None)
    return calls


def sent_payload(call):
    """Decode the JSON payload a recorded call sent (gzip or plain)."""
    data = call["data"]
    if call["headers"].get("Content-Encoding") == "gzip":
        data = gzip.decompress(data)
    return json.loads(data)


def test_post_batch_sends_gzip(monkeypatch):
    calls = patch_post(monkeypatch, [FakeResp(200, {"inserted": 5})])
    data, status = make_pipeline()._post_batch(make_spider(), "acme", make_payload(2))
    assert status == 200 and data == {"inserted": 5}
    assert calls[0]["headers"]["Content-Encoding"] == "gzip"
    assert sent_payload(calls[0])["source"] == "ashby"


def test_post_batch_falls_back_to_plain_on_gzip_400(monkeypatch):
    calls = patch_post(monkeypatch, [FakeResp(400), FakeResp(200)])
    p = make_pipeline()
    data, status = p._post_batch(make_spider(), "acme", make_payload(2))
    assert status == 200 and data is not None
    assert calls[0]["headers"].get("Content-Encoding") == "gzip"
    assert "Content-Encoding" not in calls[1]["headers"]
    assert p.gzip_enabled is False  # stays plain for the rest of the run


def test_post_batch_retries_transient_403_then_succeeds(monkeypatch):
    calls = patch_post(monkeypatch, [FakeResp(403), FakeResp(200)])
    data, status = make_pipeline()._post_batch(make_spider(), "acme", make_payload(2))
    assert status == 200 and data is not None
    assert len(calls) == 2


def test_post_batch_exits_after_two_consecutive_403s(monkeypatch):
    calls = patch_post(monkeypatch, [FakeResp(403), FakeResp(403)])
    data, status = make_pipeline()._post_batch(make_spider(), "acme", make_payload(2))
    assert data is None and status == 403
    assert len(calls) == 2  # hands off to the splitter instead of burning retries


def test_send_batch_splits_on_persistent_403(monkeypatch):
    # Full batch 403s twice, then each half succeeds.
    calls = patch_post(monkeypatch, [
        FakeResp(403), FakeResp(403),                     # full batch (4 jobs)
        FakeResp(200, {"inserted": 2, "updated": 0, "deleted": 0}),  # first half
        FakeResp(200, {"inserted": 2, "updated": 0, "deleted": 1}),  # second half
    ])
    ok, ins, upd, deleted = make_pipeline()._send_batch(
        make_spider(), "acme", make_payload(4)
    )
    assert ok is True and ins == 4 and deleted == 1
    # Halves carry 2 jobs each; dead ids / validation counts only on the first
    first, second = sent_payload(calls[2]), sent_payload(calls[3])
    assert len(first["jobs"]) == 2 and len(second["jobs"]) == 2
    assert first["dead_source_job_ids"] == ["d1"]
    assert second["dead_source_job_ids"] == []
    assert second["validation_failures"] == 0
    # Sub-batches must be flagged partial so the server skips its stale
    # sweep (otherwise each sub-batch deletes the previous one's rows)
    assert first["partial"] == 1 and second["partial"] == 1
    assert "partial" not in sent_payload(calls[0])  # full batch is not partial


def test_send_batch_single_job_403_is_lost_not_split(monkeypatch):
    calls = patch_post(monkeypatch, [FakeResp(403), FakeResp(403)])
    ok, ins, upd, deleted = make_pipeline()._send_batch(
        make_spider(), "acme", make_payload(1)
    )
    assert ok is False and (ins, upd, deleted) == (0, 0, 0)
    assert len(calls) == 2  # no infinite recursion on a 1-job batch


def test_circuit_breaker_stops_split_storm(monkeypatch):
    """When the runner IP is wholesale-blocked (every request 403s), the
    breaker must open after WAF_BLOCK_THRESHOLD lost batches: later batches
    get a single attempt and are never split — no exponential retry storm."""
    p = make_pipeline()
    spider = make_spider()

    # Batch 1: 403s at full depth — full grind is allowed (2 tries, split
    # once and twice, each leaf 2 tries... bounded by MAX_SPLIT_DEPTH).
    calls = patch_post(monkeypatch, [FakeResp(403)] * 100)
    ok, *_ = p._send_batch(spider, "c1", make_payload(4))
    assert ok is False
    first_batch_calls = len(calls)

    # Batch 2: same — after this, threshold (2) is reached.
    del calls[:]
    ok, *_ = p._send_batch(spider, "c2", make_payload(4))
    assert ok is False
    assert p._waf_blocked()

    # Batch 3: breaker open — exactly ONE attempt, no splitting.
    del calls[:]
    ok, *_ = p._send_batch(spider, "c3", make_payload(50))
    assert ok is False
    assert len(calls) == 1

    # A success closes the breaker again.
    patch_post(monkeypatch, [FakeResp(200)])
    ok, *_ = p._send_batch(spider, "c4", make_payload(2))
    assert ok is True
    assert not p._waf_blocked()
    assert first_batch_calls <= 14  # depth-capped: no exponential growth


def test_lost_batch_token_matches_ci_grep():
    """The CI workflow greps for the exact INGEST_BATCH_LOST token; make sure
    the pipeline emits it and the workflow still looks for it."""
    import inspect
    import os
    src = inspect.getsource(ingest_mod)
    assert 'INGEST_BATCH_LOST' in src

    wf = os.path.join(os.path.dirname(__file__), "..", "..",
                      ".github", "workflows", "crawl.yml")
    with open(wf, encoding="utf-8") as f:
        assert 'INGEST_BATCH_LOST' in f.read()


def test_post_batch_does_not_retry_contract_errors(monkeypatch):
    # Plain-mode 400 (gzip already off) = our bug; retrying won't help.
    calls = patch_post(monkeypatch, [FakeResp(400)])
    p = make_pipeline()
    p.gzip_enabled = False
    data, status = p._post_batch(make_spider(), "acme", make_payload(2))
    assert data is None and status == 400
    assert len(calls) == 1

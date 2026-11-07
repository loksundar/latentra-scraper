"""
End-to-end: a real `scrapy crawl` against a local mock ATS + mock ingest
endpoint, asserting the payloads the server would receive.

The unit tests drive the middleware and pipeline by hand; this one proves the
wiring Scrapy actually does — callback exceptions reaching
process_spider_exception, errbacks on 5xx, the spider_idle signal, graceful
shutdown on SIGTERM (what the workflow's `timeout` sends) — produces the
partial / heartbeat semantics the PHP endpoint relies on:

  clean    200 JSON, 1 job            → full batch (sweep allowed)
  empty    200 {"jobs": []}           → heartbeat
  waf      200 HTML                   → nothing (not reached, not healthy)
  down     503                        → nothing
  errjson  200 {"error": ...}         → nothing
  boom     200 JSON, callback raises  → batch with partial=1
  slow     answers after a delay; the crawl is SIGTERMed while it is in
           flight → every batch and heartbeat carries partial=1
"""

import gzip
import json
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

SCRAPER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PIPELINES = {
    "latentra_scraper.pipelines.ValidatePipeline": 50,
    "latentra_scraper.pipelines.NormalizePipeline": 100,
    "latentra_scraper.pipelines.EnrichPipeline": 200,
    "latentra_scraper.pipelines.IngestPipeline": 500,
}


def _job(slug, n=1):
    return {"id": n, "title": f"Software Engineer {n}", "location": {"name": "Austin, TX"},
            "departments": [{"name": "Eng"}], "content": "<p>Build things.</p>",
            "absolute_url": f"http://127.0.0.1:{{port}}/apply/{slug}/{n}", "updated_at": "2026-11-06T10:00:00Z"}


class MockATS:
    """One server plays ATS, apply pages and the ingest endpoint."""

    def __init__(self, slow_seconds=0.0):
        self.posts = []
        self.slow_seconds = slow_seconds
        self.slow_started = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, status, body=b"", ctype="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def do_HEAD(self):
                self._send(200)

            def do_GET(self):
                parts = self.path.strip("/").split("/")
                if parts[0] == "apply":
                    return self._send(200, b"ok", "text/html")
                slug = parts[1] if len(parts) > 1 else ""
                port = outer.port
                if slug == "waf":
                    return self._send(200, b"<html><body>Access denied</body></html>", "text/html")
                if slug == "down":
                    return self._send(503, b'{"error":"unavailable"}')
                if slug == "errjson":
                    return self._send(200, b'{"error":"board not found"}')
                if slug == "empty":
                    return self._send(200, b'{"jobs": [], "meta": {"total": 0}}')
                if slug == "slow":
                    outer.slow_started.set()
                    time.sleep(outer.slow_seconds)
                jobs = [_job(slug)]
                body = json.dumps({"jobs": jobs, "meta": {"total": len(jobs)}}).replace("{port}", str(port))
                return self._send(200, body.encode())

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                if self.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                outer.posts.append(json.loads(raw))
                return self._send(200, b'{"inserted": 1, "updated": 0, "deleted": 0}')

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def source(self, slug):
        return {"source_type": "greenhouse", "company_slug": slug, "company_name": slug.title(),
                "base_url": f"http://127.0.0.1:{self.port}/gh/{slug}/jobs"}


def _crawl(tmp_path, mock, slugs, extra_settings=(), sigterm_when=None, timeout=90):
    sources = tmp_path / "sources.json"
    sources.write_text(json.dumps({"greenhouse": [mock.source(s) for s in slugs]}), encoding="utf-8")
    env = dict(os.environ, SOURCES_FILE=str(sources), INGEST_URL=f"http://127.0.0.1:{mock.port}/ingest",
               INGEST_TOKEN="e2e-token", PYTHONUNBUFFERED="1")
    env.pop("SHARD_TOTAL", None)
    cmd = [sys.executable, "-m", "scrapy", "crawl", "e2e_greenhouse",
           "-s", "SPIDER_MODULES=tests.e2e_support.spiders",
           "-s", "ITEM_PIPELINES=" + json.dumps(PIPELINES),
           "-s", "RETRY_TIMES=0", "-s", "DOWNLOAD_DELAY=0", "-s", "AUTOTHROTTLE_ENABLED=0",
           "-s", "CLOSESPIDER_TIMEOUT=0", "-s", "LOG_LEVEL=INFO", *extra_settings]
    proc = subprocess.Popen(cmd, cwd=SCRAPER_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if sigterm_when is not None:
        assert sigterm_when.wait(timeout=60), "the slow request never started"
        time.sleep(0.5)                      # let the other responses land first
        proc.send_signal(signal.SIGTERM)     # what `timeout -k 60 3600` sends in crawl.yml
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        pytest.fail("crawl did not finish:\n" + out[-4000:])
    return out


@pytest.fixture
def mock():
    m = MockATS()
    yield m
    m.close()


def test_full_crawl_payload_semantics(tmp_path, mock):
    out = _crawl(tmp_path, mock, ["clean", "empty", "waf", "down", "errjson", "boom"])
    assert "Loaded 6 active greenhouse sources" in out, out[-3000:]
    by_slug = {p["company_slug"]: p for p in mock.posts}
    assert set(by_slug) == {"clean", "empty", "boom"}, (sorted(by_slug), out[-3000:])

    clean = by_slug["clean"]
    assert len(clean["jobs"]) == 1 and "partial" not in clean and "heartbeat" not in clean

    boom = by_slug["boom"]                      # first row ingested, but never a sweep
    assert len(boom["jobs"]) == 1 and boom["partial"] == 1

    empty = by_slug["empty"]
    assert empty["heartbeat"] == 1 and empty["jobs"] == [] and "partial" not in empty

    # waf / down / errjson: neither a batch nor a heartbeat
    assert "'finish_reason': 'finished'" in out
    assert "INGEST_BATCH_LOST" not in out


def test_sigterm_mid_crawl_ingests_everything_as_partial(tmp_path):
    mock = MockATS(slow_seconds=4.0)
    try:
        out = _crawl(tmp_path, mock, ["clean", "empty", "slow"], sigterm_when=mock.slow_started)
    finally:
        mock.close()
    assert "'finish_reason': 'shutdown'" in out, out[-3000:]
    by_slug = {p["company_slug"]: p for p in mock.posts}
    assert "clean" in by_slug and "empty" in by_slug, (sorted(by_slug), out[-3000:])
    assert by_slug["clean"]["partial"] == 1                 # queued pages were dropped → no sweep
    assert by_slug["empty"]["heartbeat"] == 1 and by_slug["empty"]["partial"] == 1
    assert "before the crawl finished" in out

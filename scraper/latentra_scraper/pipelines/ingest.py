"""
Ingest pipeline — validates apply_url health, then batches jobs per company
and POSTs them to the Hostinger PHP endpoint.

URL validation happens inline (replaces the separate healthcheck.py run):
  - HEAD request with browser UA
  - 404 / 410 → dead (definitive: the posting page is gone)
  - 403 / connection refused / too-many-redirects / timeouts → unknown.
    These are usually WAF/bot-blocking or transient network issues, not a
    removed posting — treating them as dead once mass-deleted live jobs.
  - 405 → fall back to GET

Dead rows are sent to the PHP endpoint as `dead_source_job_ids`. The server
deletes them only after two consecutive runs flag them (dead_count >= 2),
and they are not included in the upsert batch.
"""

import concurrent.futures
import gzip
import json
import os
import time

import requests as req
from dotenv import load_dotenv


VALIDATION_WORKERS = 50
VALIDATION_TIMEOUT = 12
BROWSER_UA = "Mozilla/5.0 (compatible; LatentraHealthCheck/2.0; +https://latentra-technologies.com)"
DEAD_CODES = (404, 410)

# Ingest POST retries — Hostinger's WAF intermittently 403s datacenter IPs
# (GitHub Actions runners); losing a whole company batch to a transient
# block is not acceptable. Batches that exhaust their retries get one more
# full pass after a cooldown (observed WAF blocks last minutes).
INGEST_RETRIES = 4
INGEST_BACKOFF_SECONDS = 45          # 45, 90, 135s between attempts
INGEST_COOLDOWN_SECONDS = 180        # pause before the second-chance pass
# 405/408 included: Hostinger's edge intermittently answers POSTs with an
# HTML 405 page (seen 2026-07-13) — transient, not a real method error.
INGEST_RETRY_CODES = (403, 405, 408, 429, 500, 502, 503, 504)

# Hard deadline for a batch's URL validation. Per-request timeouts don't
# bound "tarpit" servers that drip bytes slowly; one such URL held a
# non-daemon thread open and hung the whole process for 2.5h (job killed
# by the workflow timeout). Stragglers are marked unknown (kept alive).
VALIDATION_DEADLINE_SECONDS = 300


def _check_url(url: str):
    """Return 'alive', 'dead', or 'unknown' for a single URL."""
    if not url:
        return "dead"
    try:
        resp = req.head(
            url,
            headers={"User-Agent": BROWSER_UA},
            timeout=VALIDATION_TIMEOUT,
            allow_redirects=True,
        )
        if resp.status_code in DEAD_CODES:
            return "dead"
        if resp.status_code == 405:
            # HEAD not allowed — fall back to GET
            get_resp = req.get(
                url,
                headers={"User-Agent": BROWSER_UA},
                timeout=VALIDATION_TIMEOUT,
                allow_redirects=True,
                stream=True,
            )
            get_resp.close()
            return "dead" if get_resp.status_code in DEAD_CODES else "alive"
        return "alive"
    except Exception:
        # Connection refused, redirect loops, timeouts — could be WAF or a
        # transient outage; keep the job and let the listing API decide.
        return "unknown"


class IngestPipeline:
    """Validate URLs, batch jobs per company, and POST to the PHP ingest endpoint."""

    def __init__(self):
        self.batches = {}  # (source, company_slug) -> list of job dicts
        self.gzip_enabled = True  # flips off for the run if server can't decode

    def open_spider(self, spider):
        load_dotenv()
        self.ingest_url = os.environ.get("INGEST_URL", "")
        self.ingest_token = os.environ.get("INGEST_TOKEN", "")
        self.start_time = time.time()

        if not self.ingest_url or not self.ingest_token:
            spider.logger.warning("INGEST_URL or INGEST_TOKEN not set — skipping ingest")
            self.enabled = False
        else:
            self.enabled = True
            spider.logger.info(f"Ingest endpoint: {self.ingest_url}")

    def process_item(self, item, spider):
        if not self.enabled:
            return item
        key = (item["source"], item["company_slug"])
        if key not in self.batches:
            self.batches[key] = []
        self.batches[key].append(dict(item))
        return item

    def _validate_batch(self, jobs, spider, company_slug):
        """Validate every job's apply_url in parallel. Returns (alive, dead_ids)."""
        urls = [(j.get("source_job_id"), j.get("apply_url", "")) for j in jobs]
        statuses = {}
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=VALIDATION_WORKERS)
        future_map = {pool.submit(_check_url, url): sjid for sjid, url in urls}
        try:
            for fut in concurrent.futures.as_completed(
                future_map, timeout=VALIDATION_DEADLINE_SECONDS
            ):
                sjid = future_map[fut]
                try:
                    statuses[sjid] = fut.result()
                except Exception:
                    statuses[sjid] = "unknown"
        except concurrent.futures.TimeoutError:
            unresolved = sum(1 for f in future_map if not f.done())
            spider.logger.warning(
                f"[{company_slug}] URL validation deadline hit — "
                f"{unresolved} URL(s) unresolved, keeping them alive"
            )
        finally:
            # Don't join threads: a tarpit URL can hold one open for hours.
            pool.shutdown(wait=False, cancel_futures=True)

        alive, dead_ids = [], []
        for j in jobs:
            status = statuses.get(j.get("source_job_id"), "unknown")
            if status == "dead":
                dead_ids.append(str(j.get("source_job_id")))
            else:
                alive.append(j)

        if dead_ids:
            spider.logger.info(
                f"[{company_slug}] URL validation: {len(alive)} alive, "
                f"{len(dead_ids)} dead (will be deleted)"
            )
        return alive, dead_ids

    def close_spider(self, spider):
        if not self.enabled:
            return

        total_inserted = 0
        total_updated = 0
        total_deleted = 0

        runtime = round(time.time() - self.start_time, 2)
        validation_failures = getattr(spider, "validation_failures", 0)
        validation_warnings = getattr(spider, "validation_warnings", 0)

        deferred = []  # (company_slug, payload) — batches to re-send after cooldown
        for (source, company_slug), jobs in self.batches.items():
            alive_jobs, dead_ids = self._validate_batch(jobs, spider, company_slug)

            payload = {
                "source": source,
                "company_slug": company_slug,
                "jobs": alive_jobs,
                "dead_source_job_ids": dead_ids,
                "validation_failures": validation_failures,
                "validation_warnings": validation_warnings,
                "runtime_seconds": runtime,
            }

            ok, ins, upd, deleted = self._send_batch(spider, company_slug, payload)
            if not ok:
                deferred.append((company_slug, payload))
            total_inserted += ins
            total_updated += upd
            total_deleted += deleted

        # Second-chance pass: a WAF block usually clears within minutes, so
        # cool down once and re-send everything that exhausted its retries.
        if deferred:
            spider.logger.warning(
                f"{len(deferred)} batch(es) deferred — cooling down "
                f"{INGEST_COOLDOWN_SECONDS}s before the final retry pass"
            )
            time.sleep(INGEST_COOLDOWN_SECONDS)
            for company_slug, payload in deferred:
                ok, ins, upd, deleted = self._send_batch(spider, company_slug, payload)
                if not ok:
                    # INGEST_BATCH_LOST is the exact token CI greps to mark the
                    # run red. It must appear here and ONLY here — fuzzy phrases
                    # once false-alarmed when a server error message containing
                    # "Ingest failed" was echoed in a retry warning.
                    spider.logger.error(
                        f"INGEST_BATCH_LOST [{company_slug}] "
                        f"batch of {len(payload['jobs'])} jobs lost this run"
                    )
                total_inserted += ins
                total_updated += upd
                total_deleted += deleted

        spider.logger.info(
            f"Ingest totals: {total_inserted} inserted, {total_updated} updated, "
            f"{total_deleted} deleted"
        )

    def _send_batch(self, spider, company_slug, payload):
        """Send one company batch, splitting in half on a persistent 403.

        Hostinger's ModSecurity anomaly-scores request bodies: enough
        HTML-rich job descriptions in one payload cross the block threshold
        even though each half passes (observed on ashby/parallel). Gzip
        (in _post_batch) avoids scanning entirely once the server decodes
        it; splitting is the fallback that works against any WAF behavior.

        Returns (ok, inserted, updated, deleted). ok=False means some jobs
        were retryably lost (caller may defer/retry the payload).
        """
        data, status = self._post_batch(spider, company_slug, payload)
        if data is not None:
            ins, upd, deleted = self._tally(spider, company_slug, payload, data)
            return True, ins, upd, deleted

        jobs = payload["jobs"]
        if status == 403 and len(jobs) > 1:
            mid = len(jobs) // 2
            spider.logger.warning(
                f"[{company_slug}] WAF rejected batch of {len(jobs)} jobs — "
                f"splitting into {mid} + {len(jobs) - mid}"
            )
            # partial=1 tells the server to skip its per-company stale sweep —
            # each sub-batch only carries a slice of the run, and sweeping
            # would delete the other sub-batches' rows.
            first = dict(payload, jobs=jobs[:mid], partial=1)
            # Only the first sub-batch carries dead ids / validation counts,
            # so they aren't double-processed server-side.
            second = dict(payload, jobs=jobs[mid:], partial=1, dead_source_job_ids=[],
                          validation_failures=0, validation_warnings=0)
            ok1, i1, u1, d1 = self._send_batch(spider, company_slug, first)
            ok2, i2, u2, d2 = self._send_batch(spider, company_slug, second)
            return ok1 and ok2, i1 + i2, u1 + u2, d1 + d2

        return False, 0, 0, 0

    def _post_batch(self, spider, company_slug, payload):
        """POST one company batch with retries, gzip-compressed.

        Falls back to an uncompressed body if the server doesn't decode gzip
        yet (400 on the gzip attempt). Returns (response_json | None,
        last_http_status | None).
        """
        body = json.dumps(payload).encode("utf-8")
        last_status = None
        for attempt in range(1, INGEST_RETRIES + 1):
            headers = {
                "Authorization": f"Bearer {self.ingest_token}",
                "Content-Type": "application/json",
                "User-Agent": BROWSER_UA,
            }
            data = body
            if self.gzip_enabled:
                headers["Content-Encoding"] = "gzip"
                data = gzip.compress(body)
            try:
                resp = req.post(self.ingest_url, data=data, headers=headers, timeout=180)
                last_status = resp.status_code
                if resp.status_code == 200:
                    return resp.json(), 200
                if resp.status_code == 400 and self.gzip_enabled:
                    # Server not decoding gzip (older jobs-ingest.php) —
                    # switch the whole run to plain bodies and retry now.
                    spider.logger.warning(
                        f"[{company_slug}] Server rejected gzip body — "
                        f"falling back to uncompressed for this run"
                    )
                    self.gzip_enabled = False
                    continue
                spider.logger.warning(
                    f"[{company_slug}] Ingest attempt {attempt}/{INGEST_RETRIES} "
                    f"got HTTP {resp.status_code}: {resp.text[:200]}"
                )
                if resp.status_code not in INGEST_RETRY_CODES:
                    return None, resp.status_code  # contract errors won't fix themselves
                if resp.status_code == 403 and attempt >= 2:
                    # Two 403s in a row is a content-triggered WAF block, not
                    # a transient one — hand back to _send_batch to split.
                    return None, 403
            except (req.exceptions.RequestException, ValueError) as e:
                spider.logger.warning(
                    f"[{company_slug}] Ingest attempt {attempt}/{INGEST_RETRIES} hit: {e}"
                )
            if attempt < INGEST_RETRIES:
                time.sleep(INGEST_BACKOFF_SECONDS * attempt)
        return None, last_status

    def _tally(self, spider, company_slug, payload, data):
        """Log one batch's result, return (inserted, updated, deleted)."""
        ins = data.get("inserted", 0)
        upd = data.get("updated", 0)
        deleted = data.get("deleted", 0)
        msg = (
            f"[{company_slug}] Ingested {len(payload['jobs'])} jobs → "
            f"{ins} inserted, {upd} updated, {deleted} deleted"
        )
        anomaly = data.get("anomaly_flag")
        if anomaly:
            spider.logger.warning(msg + f" ⚠ ANOMALY: {anomaly}")
        else:
            spider.logger.info(msg)
        return ins, upd, deleted

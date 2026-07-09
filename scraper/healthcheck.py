"""
Latentra Jobs — Link Health Checker
Fetches all active job URLs from the DB via export API,
checks if they're still alive, and deactivates dead ones.

Run: python healthcheck.py
"""

import os
import re
import time
import concurrent.futures
import requests
from dotenv import load_dotenv

load_dotenv()

EXPORT_URL = os.getenv("EXPORT_URL")
HEALTHCHECK_URL = os.getenv("HEALTHCHECK_URL", EXPORT_URL.replace("jobs-export.php", "jobs-healthcheck.php") if EXPORT_URL else "")
TOKEN = os.getenv("INGEST_TOKEN")

# How many URLs to check in parallel
MAX_WORKERS = 20
# Timeout per request (seconds)
REQUEST_TIMEOUT = 15
# Only check jobs not verified in the last N hours (avoid rechecking fresh jobs)
SKIP_IF_SEEN_WITHIN_HOURS = 24

HEADERS = {"User-Agent": "LatentraHealthCheck/1.0"}
AUTH_HEADERS = {"Authorization": f"Bearer {TOKEN}"}

# ── Known "job closed" URL patterns and page content ───────────────
DEAD_URL_PATTERNS = [
    r"/404",
    r"/not-found",
    r"/job-not-found",
    r"/error",
]

# If a redirect lands on these, the job is gone
DEAD_REDIRECT_PATTERNS = [
    r"/search\??",            # redirected to search page = listing removed
    r"/jobs/?$",              # redirected to main jobs page
    r"/careers/?$",           # redirected to careers home
    r"[?&]q=",               # redirected to search with query
]

# Text on the page that indicates the job is closed/gone
DEAD_PAGE_PHRASES = [
    "this job is no longer available",
    "this position has been filled",
    "this position is no longer available",
    "this job has been closed",
    "job not found",
    "page not found",
    "no longer accepting applications",
    "this role has been filled",
    "this posting has expired",
    "sorry, this job has expired",
    "this job posting is no longer active",
    "the position you requested is no longer available",
    "this requisition is no longer active",
    "job has been removed",
    "this opening is no longer available",
    "we couldn't find that job",
    "this job is no longer posted",
]


def fetch_active_jobs():
    """Fetch all active jobs from the export API."""
    print(f"Fetching active jobs from {EXPORT_URL}...")
    resp = requests.get(EXPORT_URL, headers=AUTH_HEADERS, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    jobs = data.get("jobs", [])
    print(f"  Got {len(jobs)} active jobs")
    return jobs


def check_url(job):
    """
    Check if a job's apply_url is still alive.
    Returns (job_id, status) where status is 'alive', 'dead', or 'error'.
    """
    job_id = job["id"]
    url = job.get("apply_url", "")

    if not url:
        return (job_id, "dead", "empty URL")

    try:
        # Step 1: HEAD request (fast, checks status + redirects)
        resp = requests.head(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )

        final_url = resp.url

        # Definite dead status codes
        if resp.status_code in (404, 410, 403):
            return (job_id, "dead", f"HTTP {resp.status_code}")

        # Server error — might be temporary, don't deactivate
        if resp.status_code >= 500:
            return (job_id, "error", f"HTTP {resp.status_code}")

        # Check if redirected to a known "jobs gone" page
        for pattern in DEAD_REDIRECT_PATTERNS:
            if re.search(pattern, final_url) and not re.search(pattern, url):
                return (job_id, "dead", f"redirected to {final_url[:100]}")

        # Step 2: For 200 responses, do a GET to check page content
        # (some sites return 200 but show "job not found" in the body)
        if resp.status_code == 200:
            # Only GET a sample — some HEAD requests are unreliable, so verify
            get_resp = requests.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

            # Check final URL after redirect
            final_url = get_resp.url
            for pattern in DEAD_REDIRECT_PATTERNS:
                if re.search(pattern, final_url) and not re.search(pattern, url):
                    return (job_id, "dead", f"GET redirected to {final_url[:100]}")

            # Check page body for "job closed" phrases
            body_lower = get_resp.text[:5000].lower()  # only check first 5KB
            for phrase in DEAD_PAGE_PHRASES:
                if phrase in body_lower:
                    return (job_id, "dead", f'page says "{phrase}"')

        return (job_id, "alive", f"HTTP {resp.status_code}")

    except requests.exceptions.Timeout:
        return (job_id, "error", "timeout")
    except requests.exceptions.ConnectionError:
        return (job_id, "dead", "connection refused")
    except requests.exceptions.TooManyRedirects:
        return (job_id, "dead", "too many redirects")
    except Exception as e:
        return (job_id, "error", str(e)[:80])


def report_dead_links(dead_ids):
    """Report dead job IDs to the healthcheck API for deactivation."""
    if not dead_ids:
        print("  No dead links to report.")
        return

    print(f"  Reporting {len(dead_ids)} dead links to {HEALTHCHECK_URL}...")
    resp = requests.post(
        HEALTHCHECK_URL,
        json={"dead_ids": dead_ids},
        headers={**AUTH_HEADERS, "Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()
    print(f"  API response: {result}")


def main():
    if not EXPORT_URL or not TOKEN:
        print("ERROR: EXPORT_URL and INGEST_TOKEN must be set in .env")
        return

    start = time.time()

    # Fetch all active jobs
    jobs = fetch_active_jobs()
    if not jobs:
        print("No active jobs to check.")
        return

    # Check all URLs in parallel
    print(f"Checking {len(jobs)} URLs with {MAX_WORKERS} workers...")
    alive, dead, errors = 0, [], []
    dead_details = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(check_url, job): job for job in jobs}

        for i, future in enumerate(concurrent.futures.as_completed(futures)):
            job_id, status, reason = future.result()
            job = futures[future]

            if status == "dead":
                dead.append(job_id)
                dead_details.append((job_id, job.get("title", "")[:50], job.get("company", ""), reason))
            elif status == "error":
                errors.append((job_id, reason))
            else:
                alive += 1

            # Progress every 100 jobs
            if (i + 1) % 100 == 0:
                print(f"  Checked {i + 1}/{len(jobs)}...")

    elapsed = round(time.time() - start, 1)

    # Summary
    print(f"\n{'='*60}")
    print(f"HEALTH CHECK COMPLETE — {elapsed}s")
    print(f"{'='*60}")
    print(f"  Alive:  {alive}")
    print(f"  Dead:   {len(dead)}")
    print(f"  Errors: {len(errors)}")
    print()

    if dead_details:
        print("DEAD LINKS:")
        for jid, title, company, reason in dead_details[:50]:
            print(f"  [{jid}] {company} — {title} — {reason}")
        if len(dead_details) > 50:
            print(f"  ... and {len(dead_details) - 50} more")
        print()

    if errors:
        print(f"ERRORS (not deactivated, may be temporary):")
        for jid, reason in errors[:20]:
            print(f"  [{jid}] {reason}")
        print()

    # Report dead links to API
    report_dead_links(dead)

    print(f"Done. {len(dead)} jobs deactivated.")


if __name__ == "__main__":
    main()

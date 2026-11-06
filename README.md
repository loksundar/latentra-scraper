# Latentra Job Scraper

Scrapy pipeline that collects tech job postings from public ATS APIs
(Greenhouse, Lever, Workday, Ashby, SmartRecruiters) and pushes them to the
Latentra Jobs board at https://latentra-technologies.com/jobs.

## How it runs

GitHub Actions (`.github/workflows/crawl.yml`) crawls four times a day
(6am/9am/12pm/3pm ET).
A `sources` job fetches the active company list once (with a cached and a
committed fallback), then the five spiders run as parallel matrix jobs and
POST gzip-compressed batches to the ingest API.

Weekly source discovery (`discover.yml`) finds new company ATS boards from
curated GitHub lists, validates them, and submits them to the sources API.

## Self-healing

- **Auto-rerun** (`rerun-failed.yml`): failed crawl jobs are retried up to
  twice on fresh runners (new IPs dodge Hostinger WAF blocks).
- **Keepalive** (`keepalive.yml`): GitHub disables scheduled workflows in
  public repos after 60 days without commits — this silently stopped the
  crawl on 2026-09-14. Keepalive runs after every crawl and weekly,
  re-enables all scheduled workflows, and pushes an empty commit when the
  repo has been quiet for 40 days. Expect an occasional
  `chore: keepalive` commit; `git pull` before pushing.
- **Independent alarm**: the site's `api/cron-monitor.php`, run hourly by a
  Hostinger cron, emails the admin when sources go stale — this catches the
  case where GitHub Actions itself stops.

## Configuration

All credentials live in GitHub Actions secrets — nothing sensitive is in
this repository:

| Secret | Purpose |
|---|---|
| `INGEST_URL` | Ingest API endpoint |
| `SOURCES_URL` | Active-sources API endpoint |
| `INGEST_TOKEN` | Bearer token for both APIs |

Local development: copy `scraper/.env.example` to `scraper/.env`.

## Tests

```bash
cd scraper
python -m pytest tests/ -v
```

Golden-file regression tests pin each ATS parser's contract, plus unit tests
for enrichment (seniority/skills/location inference) and the ingest
pipeline's retry/split behavior.

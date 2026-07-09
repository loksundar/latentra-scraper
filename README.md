# Latentra Job Scraper

Scrapy pipeline that collects tech job postings from public ATS APIs
(Greenhouse, Lever, Workday, Ashby, SmartRecruiters) and pushes them to the
Latentra Jobs board at https://latentra-technologies.com/jobs.

## How it runs

GitHub Actions (`.github/workflows/crawl.yml`) crawls three times a day.
A `sources` job fetches the active company list once (with a cached and a
committed fallback), then the five spiders run as parallel matrix jobs and
POST gzip-compressed batches to the ingest API.

Weekly source discovery (`discover.yml`) finds new company ATS boards from
curated GitHub lists, validates them, and submits them to the sources API.

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

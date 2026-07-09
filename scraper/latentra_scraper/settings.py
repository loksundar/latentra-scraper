BOT_NAME = "latentra_scraper"

SPIDER_MODULES = ["latentra_scraper.spiders"]
NEWSPIDER_MODULE = "latentra_scraper.spiders"

# ── Request behavior ────────────────────────────────
# Tuned for a machine with 8 cores / 64GB RAM.
# Per-domain stays moderate (Greenhouse/Ashby/Lever share one API host each)
# but global concurrency is high because Workday tenants are separate domains.
DOWNLOAD_DELAY = 0.25
CONCURRENT_REQUESTS = 32
CONCURRENT_REQUESTS_PER_DOMAIN = 8
DOWNLOAD_TIMEOUT = 30
REACTOR_THREADPOOL_MAXSIZE = 20

ROBOTSTXT_OBEY = False

USER_AGENT = "LatentraTechJobBot/1.0 (+https://latentra-technologies.com)"

# ── AutoThrottle — adaptive rate limiting per domain ─
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 0.5
AUTOTHROTTLE_MAX_DELAY = 10
AUTOTHROTTLE_TARGET_CONCURRENCY = 6.0
AUTOTHROTTLE_DEBUG = False

# ── Retry settings ──────────────────────────────────
RETRY_ENABLED = True
RETRY_TIMES = 2
RETRY_HTTP_CODES = [429, 500, 502, 503, 504]

# ── Pipeline order ──────────────────────────────────
# Validate → Normalize → Enrich → Ingest → Export
ITEM_PIPELINES = {
    "latentra_scraper.pipelines.ValidatePipeline": 50,
    "latentra_scraper.pipelines.NormalizePipeline": 100,
    "latentra_scraper.pipelines.EnrichPipeline": 200,
    "latentra_scraper.pipelines.IngestPipeline": 500,
    "latentra_scraper.pipelines.JsonExportPipeline": 900,
}

LOG_LEVEL = "INFO"

REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
FEED_EXPORT_ENCODING = "utf-8"

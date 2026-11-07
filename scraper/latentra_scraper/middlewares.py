"""
CompanyReachMiddleware: remember which companies were checked cleanly.

A company whose ATS answered with JSON and whose callbacks ran without an
exception was scraped successfully even if it yielded 0 US tech jobs. The
ingest pipeline sends those companies an empty "heartbeat" batch so the server
records last_success_at, instead of the monitor flagging them STALE forever.

Any failure for a company in the same run (exception in a callback, request
errback, non-2xx, non-JSON body such as a WAF page, or a callback that called
spider.mark_source_failed()) disqualifies it.

Two failure sets are kept on the spider:
    companies_failed          — any failed request for the company. Blocks the
                                heartbeat.
    companies_listing_failed  — a failed LISTING-level request (anything that
                                is not a per-job detail fetch, i.e. whose
                                cb_kwargs carry no "listing"). The company's
                                job batch is then sent with partial=1 so the
                                server does not sweep the postings the crawl
                                never saw (page 2 timed out, page 3 was a WAF
                                page, ...). A single failed detail only loses
                                that one job for this run.

Requests whose body is legitimately not JSON (Apple's CSRF token endpoint)
opt out of the JSON sniff with cb_kwargs={"expect_json": False, ...}.
"""


def _key(spider, request):
    if request is None:
        return None
    src = (getattr(request, "cb_kwargs", None) or {}).get("source")
    if not isinstance(src, dict) or not src.get("company_slug"):
        return None
    return (src.get("source_type") or spider.source_type, src["company_slug"])


def _is_detail(request) -> bool:
    return "listing" in (getattr(request, "cb_kwargs", None) or {})


def mark_failed(spider, request):
    key = _key(spider, request)
    if key:
        spider.__dict__.setdefault("companies_failed", set()).add(key)
        if not _is_detail(request):
            spider.__dict__.setdefault("companies_listing_failed", set()).add(key)


def _looks_like_json(response) -> bool:
    return response.body.lstrip()[:1] in (b"{", b"[")


class CompanyReachMiddleware:
    def _ok(self, response, spider):
        request = response.request
        key = _key(spider, request)
        if not key:
            return
        if (getattr(request, "cb_kwargs", None) or {}).get("expect_json") is False:
            if not (200 <= response.status < 300):
                mark_failed(spider, request)
            return                        # neither reached nor failed on a 2xx
        if 200 <= response.status < 300 and _looks_like_json(response):
            spider.__dict__.setdefault("companies_reached", set()).add(key)
        else:
            mark_failed(spider, request)

    def process_spider_output(self, response, result, spider):
        for x in result:
            yield x
        self._ok(response, spider)        # only runs if the callback finished cleanly

    async def process_spider_output_async(self, response, result, spider):
        async for x in result:
            yield x
        self._ok(response, spider)

    def process_spider_exception(self, response, exception, spider):
        mark_failed(spider, response.request)
        return None

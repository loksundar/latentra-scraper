"""
CompanyReachMiddleware: remember which companies were checked cleanly.

A company whose ATS answered with JSON and whose callbacks ran without an
exception was scraped successfully even if it yielded 0 US tech jobs. The
ingest pipeline sends those companies an empty "heartbeat" batch so the server
records last_success_at, instead of the monitor flagging them STALE forever.

Any failure for a company in the same run (exception in a callback, request
errback, non-2xx, non-JSON body such as a WAF page) disqualifies it.
"""


def _key(spider, request):
    if request is None:
        return None
    src = (getattr(request, "cb_kwargs", None) or {}).get("source")
    if not isinstance(src, dict) or not src.get("company_slug"):
        return None
    return (src.get("source_type") or spider.source_type, src["company_slug"])


def mark_failed(spider, request):
    key = _key(spider, request)
    if key:
        spider.__dict__.setdefault("companies_failed", set()).add(key)


def _looks_like_json(response) -> bool:
    return response.body.lstrip()[:1] in (b"{", b"[")


class CompanyReachMiddleware:
    def _ok(self, response, spider):
        key = _key(spider, response.request)
        if not key:
            return
        if 200 <= response.status < 300 and _looks_like_json(response):
            spider.__dict__.setdefault("companies_reached", set()).add(key)
        else:
            mark_failed(spider, response.request)

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

"""
SmartRecruiters ATS spider.

Public API:
    GET https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=0&country=us
    GET https://api.smartrecruiters.com/v1/companies/{slug}/postings/{id}     (full job ad)

The API shape drifts. Since late 2026 the listing returns `typeOfEmployment`
and `experienceLevel` as {id, label} objects and `jobAd` as null, so the
description has to come from the detail endpoint. Every field is read through
`_label()` / `_dict()` so a str/dict/None swap can never crash the spider
again: a bad posting is skipped and counted, the rest of the company still
scrapes.
"""

import scrapy
from latentra_scraper.items import JobItem
from latentra_scraper.sources.loader import get_active_sources
from latentra_scraper.spiders.base_job_spider import BaseJobSpider

API = "https://api.smartrecruiters.com/v1/companies"


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _label(value) -> str:
    """Text from a field that may be a str, an {id,label} dict, or missing."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("label", "name", "id"):
            v = value.get(key)
            if isinstance(v, str) and v:
                return v
    return ""


class SmartRecruitersSpider(BaseJobSpider):
    name = "smartrecruiters"
    source_type = "smartrecruiters"
    allowed_domains = ["api.smartrecruiters.com"]

    PAGE_SIZE = 100
    MAX_PAGES = 30          # 3,000 US postings per company is plenty

    def _list_url(self, slug: str, offset: int) -> str:
        return f"{API}/{slug}/postings?limit={self.PAGE_SIZE}&offset={offset}&country=us"

    def build_url(self, source):
        return source.get("base_url") or self._list_url(source["company_slug"], 0)

    def start_requests(self):
        sources = get_active_sources(self.source_type)
        if not sources:
            self.logger.warning(f"No active sources for {self.source_type}")
            return
        for source in sources:
            yield scrapy.Request(
                self.build_url(source),
                callback=self.parse,
                errback=self.handle_error,
                cb_kwargs={"source": source, "offset": 0},
                dont_filter=True,
            )

    def parse(self, response, source, offset=0):
        data = self.safe_json_parse(response.text)
        if not isinstance(data, dict):
            self.logger.error(f"[{source['company_slug']}] Invalid JSON response")
            return
        content = data.get("content") or []
        if not isinstance(content, list):
            content = []
        slug = source["company_slug"]
        self.logger.info(f"[{slug}] offset={offset}, got {len(content)} postings (total: {data.get('totalFound')})")

        yield from self._handle_postings(content, source)

        next_offset = offset + self.PAGE_SIZE
        if len(content) >= self.PAGE_SIZE and next_offset < self.PAGE_SIZE * self.MAX_PAGES:
            yield scrapy.Request(
                self._list_url(slug, next_offset),
                callback=self.parse,
                errback=self.handle_error,
                cb_kwargs={"source": source, "offset": next_offset},
                dont_filter=True,
            )

    def parse_jobs(self, response, source):
        """Listing → items (old shape) or detail requests (new shape)."""
        data = self.safe_json_parse(response.text)
        content = (data.get("content") or []) if isinstance(data, dict) else []
        yield from self._handle_postings(content, source)

    def _handle_postings(self, postings, source):
        slug = source["company_slug"]
        for posting in postings:
            if not isinstance(posting, dict) or not posting.get("id"):
                continue
            loc = _dict(posting.get("location"))
            if self.skip_from_listing(_label(posting.get("name")),
                                      loc.get("fullLocation") or self._location(loc) or "",
                                      _label(loc.get("country"))):
                self.count_prefiltered()
                continue
            if _dict(_dict(posting.get("jobAd")).get("sections")):
                item = self._safe_build(posting, source)
                if item:
                    yield item
            else:
                yield scrapy.Request(
                    f"{API}/{slug}/postings/{posting['id']}",
                    callback=self.parse_detail,
                    errback=self.handle_error,
                    cb_kwargs={"source": source, "listing": posting},
                    dont_filter=True,
                )

    def parse_detail(self, response, source, listing=None):
        detail = self.safe_json_parse(response.text)
        posting = dict(listing or {})
        if isinstance(detail, dict):
            posting.update({k: v for k, v in detail.items() if v not in (None, "", {}, [])})
        item = self._safe_build(posting, source)
        if item:
            yield item

    def _safe_build(self, posting, source):
        try:
            return self._build_item(posting, source)
        except Exception as e:  # one odd posting must never sink the company
            self.logger.warning(f"[{source.get('company_slug')}] skipped posting {posting.get('id')}: {e!r}")
            if getattr(self, "crawler", None):
                self.crawler.stats.inc_value("smartrecruiters/posting_errors")
            return None

    # Kept for callers/tests that pass a plain list of postings.
    def _parse_postings(self, postings, source):
        for posting in postings or []:
            if isinstance(posting, dict) and posting.get("id"):
                item = self._safe_build(posting, source)
                if item:
                    yield item

    @staticmethod
    def _location(loc: dict):
        parts = [_label(loc.get(k)) for k in ("city", "region", "country")]
        parts = [p for p in parts if p]
        return ", ".join(parts) if parts else None

    @staticmethod
    def _employment(posting) -> str:
        emp = _dict(posting.get("typeOfEmployment"))
        raw = " ".join([_label(posting.get("typeOfEmployment")), str(emp.get("id") or "")]).lower()
        if "intern" in raw:
            return "intern"
        if "part" in raw:
            return "part_time"
        if "contract" in raw or "freelance" in raw or "temporary" in raw:
            return "contract"
        if "full" in raw or "permanent" in raw:
            return "full_time"
        return "unknown"

    def _build_item(self, posting, source):
        slug = source["company_slug"]
        posting_id = str(posting["id"])
        loc = _dict(posting.get("location"))

        if loc.get("remote") is True:
            remote_type = "remote"
        elif loc.get("hybrid") is True:
            remote_type = "hybrid"
        elif loc:
            remote_type = "onsite"
        else:
            remote_type = "unknown"

        description_html = ""
        sections = _dict(_dict(posting.get("jobAd")).get("sections"))
        for key in ("jobDescription", "qualifications", "additionalInformation", "companyDescription"):
            section = _dict(sections.get(key))
            text = section.get("text")
            if isinstance(text, str) and text:
                title = _label(section.get("title"))
                if title:
                    description_html += f"<h3>{title}</h3>"
                description_html += text

        # Apply link: the public job page. `ref` is now an API URL, never send users there.
        apply_url = f"https://jobs.smartrecruiters.com/{slug}/{posting_id}"
        for cand in (posting.get("postingUrl"), posting.get("ref")):
            if isinstance(cand, str) and cand.startswith("https://jobs.smartrecruiters.com/"):
                apply_url = cand
                break

        released = posting.get("releasedDate")
        return JobItem(
            source="smartrecruiters",
            source_job_id=posting_id,
            title=self.safe_text(_label(posting.get("name"))),
            company=self.company_display_name(source),
            company_slug=slug,
            department=_label(posting.get("department")) or None,
            seniority="unknown",       # EnrichPipeline infers it from title + description
            location=self._location(loc),
            remote_type=remote_type,
            employment_type=self._employment(posting),
            salary_min=None,
            salary_max=None,
            salary_currency=None,
            description_html=description_html,
            tags=None,
            apply_url=apply_url,
            posted_at=self.safe_datetime_iso(released) if isinstance(released, str) and released else None,
        )

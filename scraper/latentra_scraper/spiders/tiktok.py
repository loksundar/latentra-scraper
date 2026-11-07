"""
TikTok careers spider (single company, public search API).

    POST https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts
         {"keyword":"", "limit":100, "offset":N, ...empty filter lists}
         headers: website-path: tiktok

The API's country filter doesn't accept a country code, so all postings are
paged (~45 requests) and US rows kept via city_info's parent chain. Each row
already includes description + requirements.
"""

import html
import json

import scrapy

from latentra_scraper.items import JobItem
from latentra_scraper.spiders.base_job_spider import BaseJobSpider

API = "https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "website-path": "tiktok",
    "Origin": "https://lifeattiktok.com",
    "Referer": "https://lifeattiktok.com/",
}
US_NAMES = {"united states of america", "united states", "usa"}

# TikTok's job_category is clean, so filter on it: roughly half of US
# postings are Operations / Sales / HR / Legal that the title blacklist misses.
TECH_CATEGORIES = {
    "r&d", "algorithm", "product", "product manager", "data analysis", "design",
    "security", "backend", "frontend", "machine learning", "big data", "testing",
    "infrastructure", "multimedia", "interaction design", "user research",
    "project management", "client", "mobile", "data", "engineering",
}


def location_chain(city_info):
    """[city, state, country] en_names from the nested parent chain."""
    names = []
    node = city_info
    while isinstance(node, dict):
        if node.get("en_name"):
            names.append(node["en_name"])
        node = node.get("parent")
    return names


def text_to_html(text):
    if not text:
        return ""
    return "<p>" + html.escape(text).replace("\n", "<br>") + "</p>"


class TikTokSpider(BaseJobSpider):
    name = "tiktok"
    source_type = "tiktok"
    allowed_domains = ["api.lifeattiktok.com"]

    PAGE_SIZE = 100
    MAX_OFFSET = 10000

    def _req(self, source, offset):
        body = {"recruitment_id_list": [], "job_category_id_list": [], "subject_id_list": [],
                "location_code_list": [], "keyword": "", "limit": self.PAGE_SIZE, "offset": offset}
        return scrapy.Request(
            API, method="POST", headers=HEADERS, body=json.dumps(body),
            callback=self.parse_page, cb_kwargs={"source": source, "offset": offset},
            errback=self.handle_error, dont_filter=True,
        )

    def build_requests(self, source):
        yield self._req(source, 0)

    def parse_page(self, response, source, offset):
        body = self.safe_json_parse(response.text)
        data = body.get("data") if isinstance(body, dict) else None
        # Success is {"code": 0, "data": {"job_post_list": [...], "count": N}}.
        # A non-zero code / null data is an API error, not an empty board.
        if (not isinstance(data, dict) or (isinstance(body.get("code"), int) and body["code"] != 0)
                or not isinstance(data.get("job_post_list"), list)):
            self.mark_source_failed(response, f"Search page offset={offset} returned an error payload")
            return
        posts = data["job_post_list"]
        total = data.get("count") if isinstance(data.get("count"), int) else 0
        if offset == 0:
            self.logger.info(f"[tiktok] Total jobs (all countries): {total}")
        yield from self.parse_posts(posts, source)
        nxt = offset + self.PAGE_SIZE
        if posts and nxt < total and nxt < self.MAX_OFFSET:
            yield self._req(source, nxt)

    @staticmethod
    def _en_name(value) -> str:
        """en_name from a {id, en_name, ...} object, or the value itself if the
        API ever flattens it to a string."""
        if isinstance(value, dict):
            return value.get("en_name") if isinstance(value.get("en_name"), str) else ""
        return value.strip() if isinstance(value, str) else ""

    def parse_posts(self, posts, source):
        for p in posts:
            if not isinstance(p, dict):
                continue
            chain = location_chain(p.get("city_info"))
            if not chain or chain[-1].lower() not in US_NAMES:
                self.count_prefiltered()
                continue
            category = self._en_name(p.get("job_category")).strip().lower()
            if category and category not in TECH_CATEGORIES:
                self.count_prefiltered()
                continue
            post_id = p.get("id")
            if not post_id:
                continue
            location = ", ".join(chain[:-1]) + ", US" if len(chain) > 1 else "United States"
            rtype = self._en_name(p.get("recruit_type")).lower()
            employment_type = ("intern" if "intern" in rtype else
                               "full_time" if "regular" in rtype or "full" in rtype else
                               "contract" if "contract" in rtype else "unknown")
            desc = text_to_html(p.get("description") if isinstance(p.get("description"), str) else "")
            if isinstance(p.get("requirement"), str) and p["requirement"]:
                desc += "<h3>Qualifications</h3>" + text_to_html(p["requirement"])

            yield JobItem(
                source="tiktok",
                source_job_id=str(post_id),
                title=self.safe_text(p.get("title")),
                company=source.get("company_name") or "TikTok",
                company_slug=source["company_slug"],
                department=self._en_name(p.get("job_category")) or None,
                seniority="unknown",
                location=location,
                remote_type="unknown",
                employment_type=employment_type,
                salary_min=None,
                salary_max=None,
                salary_currency=None,
                description_html=desc,
                tags=None,
                apply_url=f"https://lifeattiktok.com/search/{post_id}",
                posted_at=None,
            )

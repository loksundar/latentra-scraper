"""
Workable ATS spider.

One request per company — the public careers widget returns every published
job with its full description:
    GET https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true

Rows carry `country` / `locations[].countryCode`; non-US rows are dropped
here because the free-text location ("Athens, Attica, Greece") can slip past
the validator's keyword heuristics.
"""

from latentra_scraper.items import JobItem
from latentra_scraper.spiders.base_job_spider import BaseJobSpider


class WorkableSpider(BaseJobSpider):
    name = "workable"
    source_type = "workable"
    allowed_domains = ["apply.workable.com"]

    def build_url(self, source):
        return source.get("base_url") or (
            f"https://apply.workable.com/api/v1/widget/accounts/{source['company_slug']}?details=true"
        )

    def _all_non_us(self, job) -> bool:
        """True when every country the row names is outside the US."""
        codes = [job.get("country")]
        locs = job.get("locations")
        if isinstance(locs, list):
            for loc in locs:
                if isinstance(loc, dict):
                    codes.append(loc.get("countryCode") or loc.get("country"))
        codes = [c for c in codes if isinstance(c, str) and c.strip()]
        return bool(codes) and all(self.is_non_us_country(c) for c in codes)

    def parse_jobs(self, response, source):
        data = self.safe_json_parse(response.text)
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            self.mark_source_failed(response, "Invalid JSON response (no jobs list)")
            return

        jobs = data["jobs"]
        slug = source["company_slug"]
        company = source.get("company_name") or data.get("name") or self.company_display_name(source)
        self.logger.info(f"[{slug}] Found {len(jobs)} jobs")

        for job in jobs:
            if not isinstance(job, dict):
                continue
            shortcode = job.get("shortcode")
            if not shortcode:
                continue
            if self._all_non_us(job):
                self.count_prefiltered()
                continue

            parts = [job.get("city"), job.get("state"), job.get("country")]
            location = ", ".join(p for p in parts if isinstance(p, str) and p)
            if job.get("telecommuting"):
                remote_type = "remote"
                location = location or "Remote"
            else:
                remote_type = "unknown"

            emp = self.safe_text(job.get("employment_type")).lower()
            if "full" in emp:
                employment_type = "full_time"
            elif "part" in emp:
                employment_type = "part_time"
            elif "contract" in emp or "temporary" in emp:
                employment_type = "contract"
            elif "intern" in emp:
                employment_type = "intern"
            else:
                employment_type = "unknown"

            published = job.get("published_on") or job.get("created_at")
            if isinstance(published, str) and len(published) == 10:
                posted_at = f"{published} 00:00:00"
            else:
                posted_at = self.safe_datetime_iso(published)

            url = job.get("url")
            yield JobItem(
                source="workable",
                source_job_id=str(shortcode),
                title=self.safe_text(job.get("title")),
                company=company,
                company_slug=slug,
                department=self.safe_text(job.get("department")) or None,
                seniority="unknown",
                location=location,
                remote_type=remote_type,
                employment_type=employment_type,
                salary_min=None,
                salary_max=None,
                salary_currency=None,
                description_html=job.get("description") if isinstance(job.get("description"), str) else "",
                tags=None,
                apply_url=url if isinstance(url, str) and url else f"https://apply.workable.com/{slug}/j/{shortcode}/",
                posted_at=posted_at,
            )

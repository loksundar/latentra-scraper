"""
Ashby ATS spider.

Ashby provides a clean public JSON API at:
    GET https://api.ashbyhq.com/posting-api/job-board/{slug}

Returns all jobs in a single response (no pagination needed).
Each job includes: title, department, employmentType, location, isRemote,
workplaceType, publishedAt, descriptionHtml, descriptionPlain, applyUrl, and
address.postalAddress.addressCountry (used for the US prefilter — the
free-text `location` is often just a city).
"""

from latentra_scraper.items import JobItem
from latentra_scraper.spiders.base_job_spider import BaseJobSpider


class AshbySpider(BaseJobSpider):
    name = "ashby"
    source_type = "ashby"
    allowed_domains = ["api.ashbyhq.com"]

    def build_url(self, source):
        slug = source["company_slug"]
        base = source.get("base_url")
        if base:
            return base
        return f"https://api.ashbyhq.com/posting-api/job-board/{slug}"

    @staticmethod
    def _country(job) -> str:
        addr = job.get("address") if isinstance(job, dict) else None
        postal = addr.get("postalAddress") if isinstance(addr, dict) else None
        if isinstance(postal, dict) and isinstance(postal.get("addressCountry"), str):
            return postal["addressCountry"]
        return ""

    def _us_location(self, job):
        """
        The location text to publish, or None when the posting is clearly not
        US. Ashby postings carry a primary address plus `secondaryLocations`
        (each with its own address); live boards list e.g. primary
        "Toronto, ON" with secondaries "Remote (US)" / "Miami, FL". Such a
        posting IS a US job — skip it only when every address names another
        country, and when the primary is foreign show the first US secondary.
        """
        primary = self._country(job)
        if not self.is_non_us_country(primary):
            return self.safe_text(job.get("location"))
        secondaries = job.get("secondaryLocations")
        for sec in secondaries if isinstance(secondaries, list) else []:
            if isinstance(sec, dict) and sec.get("address") and not self.is_non_us_country(self._country(sec)):
                return self.safe_text(sec.get("location")) or self.safe_text(job.get("location"))
        return None

    def parse_jobs(self, response, source):
        data = self.safe_json_parse(response.text)
        # The board API always answers {"jobs": [...], "apiVersion": ...}.
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            self.mark_source_failed(response, "Invalid JSON response (no jobs list)")
            return

        jobs = data["jobs"]
        company_slug = source["company_slug"]
        company_name = self.company_display_name(source)
        self.logger.info(f"[{company_slug}] Found {len(jobs)} jobs")

        for job in jobs:
            if not isinstance(job, dict):
                continue
            if not job.get("isListed", True):
                continue
            location = self._us_location(job)
            if location is None:
                self.count_prefiltered()
                continue

            # Remote type
            is_remote = job.get("isRemote")
            workplace = self.safe_text(job.get("workplaceType")).lower()
            if is_remote is True or workplace == "remote":
                remote_type = "remote"
            elif workplace == "hybrid":
                remote_type = "hybrid"
            elif workplace in ("onsite", "on-site"):
                remote_type = "onsite"
            else:
                remote_type = "unknown"

            # Employment type
            emp = self.safe_text(job.get("employmentType")).lower()
            if "full" in emp:
                employment_type = "full_time"
            elif "part" in emp:
                employment_type = "part_time"
            elif "contract" in emp or "freelance" in emp:
                employment_type = "contract"
            elif "intern" in emp:
                employment_type = "intern"
            else:
                employment_type = "unknown"

            department = job.get("department") or job.get("team")
            apply_url = job.get("applyUrl") or job.get("jobUrl")

            yield JobItem(
                source="ashby",
                source_job_id=self.safe_text(job.get("id")),
                title=self.safe_text(job.get("title")),
                company=company_name,
                company_slug=company_slug,
                department=self.safe_text(department) or None,
                seniority="unknown",
                location=location,
                remote_type=remote_type,
                employment_type=employment_type,
                salary_min=None,
                salary_max=None,
                salary_currency=None,
                description_html=job.get("descriptionHtml") if isinstance(job.get("descriptionHtml"), str) else "",
                tags=None,
                apply_url=apply_url if isinstance(apply_url, str) else "",
                posted_at=self.safe_datetime_iso(job.get("publishedAt")),
            )

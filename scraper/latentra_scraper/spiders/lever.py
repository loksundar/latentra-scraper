from latentra_scraper.items import JobItem
from latentra_scraper.spiders.base_job_spider import BaseJobSpider


class LeverSpider(BaseJobSpider):
    name = "lever"
    source_type = "lever"
    allowed_domains = ["api.lever.co"]

    def build_url(self, source):
        slug = source["company_slug"]
        base = source.get("base_url")
        if base:
            return base
        return f"https://api.lever.co/v0/postings/{slug}?mode=json"

    def parse_jobs(self, response, source):
        jobs = self.safe_json_parse(response.text)
        if not isinstance(jobs, list):
            self.logger.error(f"[{source['company_slug']}] Unexpected response format")
            return

        company_slug = source["company_slug"]
        company_name = self.company_display_name(source)
        self.logger.info(f"[{company_slug}] Found {len(jobs)} jobs")

        for job in jobs:
            categories = job.get("categories", {})

            # Location
            location = categories.get("location", "") or ""

            # Remote type
            workplace = (job.get("workplaceType") or "").lower()
            if workplace == "remote":
                remote_type = "remote"
            elif workplace == "hybrid":
                remote_type = "hybrid"
            elif workplace in ("on-site", "onsite"):
                remote_type = "onsite"
            else:
                remote_type = "unknown"

            # Employment type
            commitment = (categories.get("commitment") or "").lower()
            if "full" in commitment:
                employment_type = "full_time"
            elif "part" in commitment:
                employment_type = "part_time"
            elif "contract" in commitment or "freelance" in commitment:
                employment_type = "contract"
            elif "intern" in commitment:
                employment_type = "intern"
            else:
                employment_type = "unknown"

            # Description — combine description + lists + additional
            description_html = job.get("description", "")
            for lst in job.get("lists", []):
                description_html += f"<h3>{lst.get('text', '')}</h3>"
                description_html += "<ul>"
                for item in lst.get("content", "").split("</li>"):
                    if item.strip():
                        description_html += item + "</li>"
                description_html += "</ul>"
            additional = job.get("additional", "")
            if additional:
                description_html += additional

            # Department
            department = categories.get("team") or None

            yield JobItem(
                source="lever",
                source_job_id=str(job.get("id", "")),
                title=self.safe_text(job.get("text")),
                company=company_name,
                company_slug=company_slug,
                department=department,
                seniority="unknown",
                location=location,
                remote_type=remote_type,
                employment_type=employment_type,
                salary_min=None,
                salary_max=None,
                salary_currency=None,
                description_html=description_html,
                tags=None,
                apply_url=job.get("hostedUrl", ""),
                posted_at=self.safe_datetime_epoch_ms(job.get("createdAt")),
            )

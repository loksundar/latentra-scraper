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
            # Lever answers {"ok": false, "error": "Document not found"} (and
            # other error objects) with a JSON body — that is not an empty board.
            self.mark_source_failed(response, "Unexpected response format (not a postings list)")
            return

        company_slug = source["company_slug"]
        company_name = self.company_display_name(source)
        self.logger.info(f"[{company_slug}] Found {len(jobs)} jobs")

        for job in jobs:
            if not isinstance(job, dict):
                continue
            # Every posting carries an ISO country code ("US", "GB", ...). It is
            # far more reliable than the free-text location for the US filter.
            if self.is_non_us_country(job.get("country")):
                self.count_prefiltered()
                continue

            categories = job.get("categories")
            if not isinstance(categories, dict):
                categories = {}

            # Location
            location = self.safe_text(categories.get("location"))

            # Remote type
            workplace = self.safe_text(job.get("workplaceType")).lower()
            if workplace == "remote":
                remote_type = "remote"
            elif workplace == "hybrid":
                remote_type = "hybrid"
            elif workplace in ("on-site", "onsite"):
                remote_type = "onsite"
            else:
                remote_type = "unknown"

            # Employment type
            commitment = self.safe_text(categories.get("commitment")).lower()
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
            description_html = job.get("description") if isinstance(job.get("description"), str) else ""
            lists = job.get("lists") if isinstance(job.get("lists"), list) else []
            for lst in lists:
                if not isinstance(lst, dict):
                    continue
                content = lst.get("content") if isinstance(lst.get("content"), str) else ""
                description_html += f"<h3>{self.safe_text(lst.get('text'))}</h3>"
                description_html += "<ul>"
                for item in content.split("</li>"):
                    if item.strip():
                        description_html += item + "</li>"
                description_html += "</ul>"
            additional = job.get("additional")
            if isinstance(additional, str) and additional:
                description_html += additional

            # Department
            department = self.safe_text(categories.get("team")) or None

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
                apply_url=job.get("hostedUrl") if isinstance(job.get("hostedUrl"), str) else "",
                posted_at=self.safe_datetime_epoch_ms(job.get("createdAt")),
            )

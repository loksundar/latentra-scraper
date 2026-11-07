"""
Spider used by tests/test_e2e_crawl.py. It is the real Greenhouse spider
pointed at a local mock ATS (allowed_domains cleared so 127.0.0.1 is not
filtered as offsite), plus one deliberate callback crash for the company
"boom" — the shape-drift case the middleware must turn into partial=1.
"""

from latentra_scraper.spiders.greenhouse import GreenhouseSpider


class E2EGreenhouseSpider(GreenhouseSpider):
    name = "e2e_greenhouse"
    allowed_domains = []

    def parse_jobs(self, response, source):
        yield from super().parse_jobs(response, source)
        if source["company_slug"] == "boom":
            raise ValueError("simulated ATS shape drift after the first row")

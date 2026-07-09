"""
Normalization pipeline — cleans raw items from spiders.
Handles only: HTML stripping, field defaults.
Enrichment (skills, seniority, location, platforms) is in EnrichPipeline.
"""

import html as html_module
import re


class NormalizePipeline:
    """Clean and normalize job items coming from any spider."""

    def process_item(self, item, spider):
        # Strip HTML → plain text
        raw_html = item.get("description_html") or ""
        if not item.get("description_text"):
            item["description_text"] = self._strip_html(raw_html)

        # Defaults for missing fields
        if not item.get("employment_type"):
            item["employment_type"] = "unknown"
        if not item.get("remote_type"):
            item["remote_type"] = "unknown"
        if not item.get("seniority"):
            item["seniority"] = "unknown"
        for f in ("salary_min", "salary_max", "salary_currency"):
            if not item.get(f):
                item[f] = None
        if not item.get("department"):
            item["department"] = None

        return item

    def _strip_html(self, html):
        text = html_module.unescape(html)
        text = re.sub(r"<br\s*/?>", "\n", text)
        text = re.sub(r"<li>", "\n- ", text)
        text = re.sub(r"</p>", "\n\n", text)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

"""
Validation round 4 — regression review of the round-3 data-quality changes.
Every US job that round 3 could lose or corrupt:

  * US filter: whole-word non-US matching ("New England", "St. Tammany
    Parish", "Londonderry" were substring hits on england/paris/london),
    word-bounded US signals ("US & Canada", "Remote (US or Canada)",
    "US/Canada"), digits are not ISO-code boundaries ("4TH FLOOR" ≠ Thailand).
  * Title filter: the IT override group ends in \\b, so "product design" never
    matched "Product Designer" — "Product Designer, Payroll" was a payroll job;
    Amazon "Applied Science Internship … Science Recruiting" was recruiting;
    HR/Finance Technology, Data Products and Data Center roles were business.
  * posted_at: DST boundaries, date-only and naive values, epoch ms.
  * Determinism: primary_cloud_platforms tie order followed the hash seed.
  * Contract: identity columns are never truncated, valid enums never nulled.
"""

from datetime import datetime, timedelta, timezone

import pytest
from scrapy.exceptions import DropItem

from latentra_scraper.enrichment.platform_keywords import extract_platform_keywords
from latentra_scraper.items import JobItem
from latentra_scraper.pipelines.enrich import EnrichPipeline
from latentra_scraper.pipelines.normalize import NormalizePipeline
from latentra_scraper.pipelines.validate import ValidatePipeline
from latentra_scraper.schema import ENUMS, MAX_LEN_IDENTITY, conform_item
from latentra_scraper.spiders.base_job_spider import BaseJobSpider


def _item(**over):
    base = dict(
        source="greenhouse", source_job_id="1", title="Software Engineer", company="Acme",
        company_slug="acme", department="Eng", seniority="unknown", location="Austin, TX",
        remote_type="unknown", employment_type="unknown", salary_min=None, salary_max=None,
        salary_currency=None, description_html="<p>Build things.</p>", tags=None,
        apply_url="https://x.example/1", posted_at="2026-11-01 00:00:00",
    )
    base.update(over)
    return JobItem(**base)


def _enrich(**over):
    item = _item(**over)
    NormalizePipeline().process_item(item, None)
    return EnrichPipeline().process_item(item, None)


# ── 1. US filter: real-looking US locations that collide with the lists ──

US_LOCATIONS = [
    # State vs country / city collisions, with a state code or name
    "Atlanta, Georgia", "Georgia", "Savannah, GA", "Paris, TX", "Paris, Texas", "Rome, GA", "Rome, New York",
    "Lima, OH", "Delhi, NY", "Athens, GA", "Athens, Ohio", "Dublin, CA", "Dublin, Ohio", "Cambridge, MA",
    "Cambridge", "London, KY", "New London, CT", "Londonderry, NH", "Londonderry", "Manchester, NH",
    "Manchester, New Hampshire", "Birmingham, AL", "Birmingham", "Toledo, OH", "Toledo", "Berlin, NH",
    "New Berlin, WI", "Columbia, MD", "Columbia, SC", "Columbia", "Jordan, MN", "West Jordan, UT", "West Jordan",
    "Vienna, VA", "Vienna, Virginia", "Geneva, IL", "Warsaw, IN", "Lebanon, PA", "Lebanon, Tennessee",
    "Peru, IL", "Mexico, MO", "New Mexico", "Albuquerque, New Mexico", "Melbourne, FL", "Perth Amboy, NJ",
    "Waterloo, IA", "Ontario, CA", "Ontario, California", "Vancouver, WA", "Vancouver, Washington",
    "Ottawa, IL", "Hamburg, NY", "Cologne, MN", "Stuttgart, AR", "Milan, TN", "Brazil, IN", "Italy, TX",
    "Poland, ME", "Norway, ME", "Denmark, SC", "Finland, MN", "Greece, NY", "Prague, OK", "Belgrade, MT",
    "Lisbon, OH", "Cairo, IL", "Malta, NY", "Amsterdam, NY", "Madrid, IA", "Moscow, ID", "Odessa, TX",
    "Wales, WI", "North Wales, PA", "Scotland, PA", "Leeds, AL", "Belfast, ME", "Glasgow, KY",
    "Wellington, FL", "Brisbane, CA", "Sydney, NE", "Indian Wells, CA", "Indiana", "Indianapolis, IN",
    "Milwaukee, WI", "Milwaukee", "Newark, DE", "Wilmington, Delaware", "Fort Collins, CO", "Denver, CO",
    # Regions
    "Remote - New England", "New England", "Boston / New England", "Jefferson Parish, LA",
    "St. Tammany Parish", "SF Bay Area", "NYC", "New York City", "Washington, D.C.", "Washington DC",
    "Washington, DC Metro Area", "DC", "Puerto Rico", "San Juan, PR", "Guaynabo, Puerto Rico",
    "Honolulu, HI", "Anchorage, AK", "Pacific Northwest", "Midwest", "East Coast", "Nationwide",
    # Remote / US phrasing
    "Remote - US", "US Remote", "US-Remote", "Remote, USA", "Remote, US", "Anywhere in the US",
    "United States (Remote)", "Remote (United States)", "Remote - United States", "USA - Remote",
    "US", "U.S.", "U.S.A.", "USA", "United States", "United States of America", "Virtual - US",
    "Remote - US/Canada", "US & Canada", "US or Canada - Remote", "Remote (US or Canada)",
    "United States or Canada", "Remote - North America", "Remote - Americas", "Remote - AMER",
    "Remote - EST", "Remote (Eastern Time)", "Remote - Pacific Time Zone", "Hybrid - Boston",
    "Hybrid - Austin, TX", "Remote", "Anywhere", "Multiple Locations",
    # Workday / Oracle style
    "US-NY-New York", "US-OH-Dayton", "US-GA-Atlanta 5TH Floor", "350 5TH AVE, 4TH FLOOR", "USA-CA-Palo Alto",
    "Seattle, Washington, USA", "San Francisco, CA, United States", "New York, NY 10001",
    "1600 Amphitheatre Pkwy, Mountain View", "Plano, TX; Charlotte, NC", "Austin, TX / Remote",
    "Denver, CO - Hybrid; Toronto, Ontario - Remote",
    "Seattle, San Francisco, New York, Chicago, Atlanta, Remote in the US, Canada",
]

NON_US_LOCATIONS = [
    "London", "London, United Kingdom", "Remote - United Kingdom", "Remote - UK", "Manchester, UK",
    "Paris", "Paris, France", "Rome, Italy", "Athens, Greece", "Dublin", "Dublin, Ireland", "Dublin, IE",
    "Berlin", "Berlin, Germany", "Munich", "Vienna, Austria", "Geneva, Switzerland", "Zürich, CH",
    "Toronto", "Toronto, ON", "Vancouver, BC", "Vancouver, British Columbia", "Halifax, Nova Scotia",
    "Remote - Canada", "Canada", "Bengaluru", "Bangalore, India", "Hyderabad", "Pune", "Remote - India",
    "Sydney", "Sydney, Australia", "Melbourne, Australia", "Singapore", "Tokyo, Japan", "Tel Aviv, Israel",
    "São Paulo", "Sao Paulo, Brazil", "Mexico City", "Buenos Aires", "Bogotá, Colombia", "Lima, Peru",
    "Amman, Jordan", "Dubai", "UAE - Remote", "Remote - EMEA", "Remote - APAC", "Remote - LATAM",
    "Belgrade, Serbia", "Warsaw", "Remote - Poland", "Lisbon", "Madrid", "Barcelona, Spain", "Amsterdam",
    "Stockholm", "Oslo - Remote", "Copenhagen", "Helsinki", "Remote (Sweden)", "Aarhus, Denmark",
    "Belgium", "Luxembourg", "Auckland, New Zealand", "Cape Town", "Nairobi, Kenya", "Lagos, Nigeria",
    "Markham, ON", "Ontario, CAN", "DEU - Remote - Munich", "AUS-Remote", "IND-Bengaluru",
    "4TH FLOOR, Sukhumvit Rd, Bangkok", "Dachau, Germany", "Cambridgeshire, England",
]


class TestUSFilterRound4:
    def test_table_is_large_enough(self):
        assert len(US_LOCATIONS) >= 60 and len(set(US_LOCATIONS)) == len(US_LOCATIONS)

    @pytest.mark.parametrize("loc", US_LOCATIONS)
    def test_us_locations_are_kept(self, loc):
        assert ValidatePipeline()._is_likely_us({"location": loc}) is True, loc

    @pytest.mark.parametrize("loc", NON_US_LOCATIONS)
    def test_non_us_locations_are_still_dropped(self, loc):
        assert ValidatePipeline()._is_likely_us({"location": loc}) is False, loc

    def test_us_job_survives_the_whole_validator(self):
        for loc in ("Remote - New England", "US & Canada", "St. Tammany Parish", "350 5TH AVE, 4TH FLOOR"):
            assert ValidatePipeline().process_item(_item(location=loc), None) is not None


# ── 2. Title filter: tech roles the business-function rules must not eat ──

TECH_TITLES_KEPT = [
    "Product Designer, Payroll", "Senior Product Designer, Growth Marketing", "Product Designer, Recruiting Products",
    "Staff Product Designer, People Team", "UX Designer, HR Tools", "Interaction Designer, Accounting",
    "Design Systems Lead, Brand", "Design Technologist, Marketing",
    "2027 Applied Science Internship - Computer Vision - United States, PhD Student Science Recruiting",
    "2027 Research Science Internship - United States, PhD Student Science Recruiting",
    "Applied Science Manager, Recruiting", "Data Science Manager, Sales",
    "Data Center Facilities Technician", "Data Center Technician, Facilities", "Data Center Operations, Procurement",
    "HR Technology Analyst", "People Technology Lead", "Recruiting Technology Lead", "Finance Systems Manager",
    "Accounting Data & Technology Manager", "Sales Systems Manager", "Marketing Technology Manager, HR",
    "Senior Platform Manager, Data Products, Finance Accounting", "Data Product Manager, Payroll",
    "IT Administrator, Facilities", "IT Operations Analyst, Payroll", "IT Technician, Office Manager Support",
    "Network Administrator, Payroll", "System Administrator, HR", "Systems Administrator, Recruiting",
    "Technical Customer Support Specialist", "Technical Support Engineer", "Technical Product Support Specialist",
    # Round-3 keeps must still hold
    "Sales Engineer", "Marketing Data Analyst", "Finance Systems Analyst", "Technical Account Manager",
    "HRIS Analyst (Workday)", "Product Manager, Payments", "Recruiting Platform Engineer", "Payroll Systems Engineer",
]

BUSINESS_TITLES_DROPPED = [
    "Account Executive | Enterprise", "Sales Development Representative", "Senior Tax Accountant",
    "Technical Recruiter", "Technical Sourcer", "HR Business Partner", "Executive Assistant", "Product Marketing Manager",
    "Brand Designer, Creative Studio", "Cybersecurity Sales Specialist", "Customer Success Manager", "Payroll Analyst",
    "Partner Marketing Manager, Technology Partners & ISVs", "Procurement Operations Business Partner, Technology",
    "Field Marketing Lead, Public Sector", "Strategic Finance Analyst", "Commercial Counsel, GTM", "Recruiting Coordinator",
    "Radiologist", "Landscaper", "Forklift Operator",
]


class TestTitleFilterRound4:
    def _drops(self, title):
        try:
            ValidatePipeline().process_item(_item(title=title), None)
        except DropItem as e:
            return "Non-IT" in str(e)
        return False

    @pytest.mark.parametrize("title", TECH_TITLES_KEPT)
    def test_tech_titles_are_kept(self, title):
        assert not self._drops(title), title

    @pytest.mark.parametrize("title", BUSINESS_TITLES_DROPPED)
    def test_business_titles_still_drop(self, title):
        assert self._drops(title), title


# ── 3. Contract: identity columns and enums ──────────────────────────────

class TestContractRound4:
    def test_conform_never_touches_identity_columns(self):
        long_url = "https://x.example/" + "a" * 600
        long_id = "j" * 150
        item = _item(apply_url=long_url, source_job_id=long_id, source="greenhousegreenhouse1234")
        conform_item(item)
        assert item["apply_url"] == long_url and item["source_job_id"] == long_id
        assert item["source"] == "greenhousegreenhouse1234"

    @pytest.mark.parametrize("field", list(MAX_LEN_IDENTITY))
    def test_validator_drops_oversize_identity_instead(self, field):
        item = _item(**{field: "x" * (MAX_LEN_IDENTITY[field] + 1)})
        with pytest.raises(DropItem) as e:
            ValidatePipeline().process_item(item, None)
        assert "too long" in str(e.value)

    def test_every_server_enum_value_round_trips(self):
        for field, allowed in ENUMS.items():
            for value in allowed:
                item = _item(**{field: value})
                conform_item(item)
                assert item[field] == value, (field, value)

    def test_enriched_item_always_has_non_empty_required_scalars(self):
        """jobs-ingest.php 400-rejects the whole batch when any job lacks a
        scalar non-empty source_job_id / title / apply_url."""
        item = _enrich(title="  " + "T" * 350 + "  ")
        for f in ("source_job_id", "title", "apply_url"):
            assert isinstance(item[f], str) and item[f].strip(), f
        assert len(item["title"]) <= 300

    def test_whitespace_only_required_fields_are_dropped_before_ingest(self):
        for f in ("source_job_id", "title", "apply_url"):
            with pytest.raises(DropItem):
                ValidatePipeline().process_item(_item(**{f: "   "}), None)


# ── 4. posted_at on the server clock ─────────────────────────────────────

class TestPostedAtRound4:
    @pytest.mark.parametrize("value,expected", [
        # Spring forward 2026-03-08 08:00 UTC: 01:59:59 CST then 03:00:00 CDT
        ("2026-03-08T07:59:59Z", "2026-03-08 01:59:59"),
        ("2026-03-08T08:00:00Z", "2026-03-08 03:00:00"),
        # Fall back 2026-11-01 07:00 UTC: 01:30 CDT, then 01:30 CST again
        ("2026-11-01T06:30:00Z", "2026-11-01 01:30:00"),
        ("2026-11-01T07:30:00Z", "2026-11-01 01:30:00"),
        # Offsets other than UTC
        ("2026-06-20T12:34:56-04:00", "2026-06-20 11:34:56"),
        ("2026-06-20T12:34:56+05:30", "2026-06-20 02:04:56"),
        ("2026-06-20T12:34:56.123456789Z", "2026-06-20 07:34:56"),
        ("2026-06-20T12:34:56.000+00:00", "2026-06-20 07:34:56"),
    ])
    def test_aware_values_convert_to_chicago(self, value, expected):
        assert BaseJobSpider.safe_datetime_iso(value) == expected

    @pytest.mark.parametrize("value,expected", [
        ("2026-11-07", "2026-11-07 00:00:00"),            # date only: never shifted
        ("2026-11-07T00:00:00", "2026-11-07 00:00:00"),   # naive: passed through
        (" 2026-11-07 ", "2026-11-07 00:00:00"),
    ])
    def test_dates_and_naive_values_keep_their_day(self, value, expected):
        assert BaseJobSpider.safe_datetime_iso(value) == expected

    def test_epoch_ms_boundaries(self):
        # 2026-11-01 07:00:00 UTC is the fall-back instant → 01:00 CST
        assert BaseJobSpider.safe_datetime_epoch_ms(1793516400000) == "2026-11-01 01:00:00"
        assert BaseJobSpider.safe_datetime_epoch_ms("1793516400000") == "2026-11-01 01:00:00"
        # one ms before the spring-forward instant is still CST
        assert BaseJobSpider.safe_datetime_epoch_ms(1772956799999) == "2026-03-08 01:59:59"
        assert BaseJobSpider.safe_datetime_epoch_ms(0) is None           # falsy → None
        assert BaseJobSpider.safe_datetime_epoch_ms(10 ** 20) is None    # overflow → None
        assert BaseJobSpider.safe_datetime_epoch_ms(-10 ** 18) is None

    def test_conversion_never_produces_a_future_timestamp(self):
        now = datetime.now(timezone.utc)
        out = BaseJobSpider.safe_datetime_iso(now.isoformat())
        parsed = datetime.strptime(out, "%Y-%m-%d %H:%M:%S")
        # Compared on the server clock (America/Chicago), never ahead of it.
        from latentra_scraper.spiders.base_job_spider import _SERVER_TZ
        assert parsed <= now.astimezone(_SERVER_TZ).replace(tzinfo=None) + timedelta(seconds=1)

    def test_today_in_utc_evening_is_not_rejected_as_future_or_old(self):
        out = BaseJobSpider.safe_datetime_iso((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
        assert ValidatePipeline()._is_too_old(out) is False


# ── 5. Determinism ───────────────────────────────────────────────────────

class TestDeterminismRound4:
    def test_primary_cloud_ties_are_ordered_by_name(self):
        r = extract_platform_keywords("We use AWS and Azure and GCP daily.")
        assert r["primary_cloud_platforms"] == ["AWS", "Azure", "GCP"]
        r = extract_platform_keywords("GCP GCP GCP, and some Azure and AWS.")
        assert r["primary_cloud_platforms"] == ["GCP", "AWS", "Azure"]

    def test_enrichment_output_is_identical_across_runs(self):
        desc = ("<p>Fully remote. We run on AWS and Azure with Kubernetes, Python, Spark SQL, Rails apps "
                "and Spring Boot. 5+ years experience. $150,000 - $180,000.</p>")
        a = dict(_enrich(description_html=desc, location="Remote - US"))
        b = dict(_enrich(description_html=desc, location="Remote - US"))
        assert a == b
        assert a["skills"] == sorted(a["skills"])

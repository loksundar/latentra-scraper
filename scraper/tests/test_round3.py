"""
Validation round 3 — client/server contract, enrichment correctness on real
data, and ValidatePipeline rules. Every case below reproduces something seen on
a live crawl (Greenhouse / Lever / Ashby / Workable / Workday, Nov 2026) or on
the 6.7k-job local-dev sample:

  * EnrichPipeline flipped 41% of jobs to remote_type=remote because the
    description contained "distributed systems" / "remote teams"
  * Ashby: isRemote=true + workplaceType=Hybrid → "remote" (178/325 postings)
  * Greenhouse multi-location "San Francisco, CA; Chicago, IL & New York, NY"
    → city "San Francisco", state NY (';' / '&' not split); "New York, NY" and
    "Washington, DC" → city None (state name == city name)
  * Validator kept "Remote - United Kingdom", "Belgium - Remote", "UAE - Remote",
    "Oslo - Remote", "Zürich, CH", "Dublin, IE" (the remote branch returned
    before the country check; many countries missing)
  * Validator kept 24% non-tech business titles (AE, recruiter, accountant...)
  * Seniority: every Product/Program Manager was "manager"; "Senior Manager"
    was "senior"; "Product Manager Intern" was "manager"; SDE II was "entry"
  * Skills: "spark innovation", "spring 2027", "multi-node", "Adobe Express",
    "swift resolution", "crypto rails" counted as technologies
  * Contract: 415-char Greenhouse locations vs VARCHAR(300); booleans sent as
    JSON false (PDO binds "" into TINYINT); posted_at sent as UTC/ATS-local
    wall-clock while the server stamps America/Chicago
"""

import json

import pytest
from scrapy.exceptions import DropItem

from latentra_scraper.enrichment.location import normalize_location
from latentra_scraper.enrichment.seniority import infer_seniority
from latentra_scraper.enrichment.skills import extract_skills
from latentra_scraper.items import JobItem
from latentra_scraper.pipelines.enrich import EnrichPipeline
from latentra_scraper.pipelines.normalize import NormalizePipeline
from latentra_scraper.pipelines.validate import ValidatePipeline
from latentra_scraper.schema import conform_item
from latentra_scraper.spiders.ashby import AshbySpider
from latentra_scraper.spiders.base_job_spider import BaseJobSpider
from tests.conftest import fake_response, load_fixture


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


# ── location parsing ───────────────────────────────────────────────────

class TestLocationParsing:
    @pytest.mark.parametrize("raw,city,state", [
        ("San Francisco, CA; Chicago, IL & New York, NY", "San Francisco", "CA"),
        ("Chicago, IL; New York, NY; San Francisco, CA", "Chicago", "IL"),
        ("New York, NY", "New York", "NY"),
        ("New York, NY (HQ)", "New York", "NY"),
        ("Washington, D.C.", "Washington", "DC"),
        ("Washington, DC", "Washington", "DC"),
        ("Seattle, San Francisco, New York, Chicago, Atlanta, Remote in the US, Canada", "Seattle", "WA"),
        ("Denver, CO - Hybrid; New York, NY - Hybrid", "Denver", "CO"),
        ("San Francisco or Palo Alto", "San Francisco", "CA"),
        ("Portland, OR", "Portland", "OR"),
        ("Bellevue, Washington; Mountain View, California", "Bellevue", "WA"),
        ("New York City", "New York City", "NY"),
        ("South San Francisco", "South San Francisco", "CA"),
        ("USA - Remote - Los Angeles", "Los Angeles", "CA"),
    ])
    def test_first_city_state_pair_wins(self, raw, city, state):
        r = normalize_location(raw)
        assert (r["location_city"], r["location_state"], r["location_country"]) == (city, state, "United States")

    def test_multi_location_with_a_us_office_is_a_us_job(self):
        # Was: country "Canada" because Canada was the only country word.
        r = normalize_location("Seattle, San Francisco, New York, Chicago, Atlanta, Remote in the US, Canada")
        assert r["location_country"] == "United States"
        r = normalize_location("Bellevue, Washington; Mountain View, California; Vancouver, Canada")
        assert r["location_country"] == "United States"

    @pytest.mark.parametrize("raw", ["Remote (US)", "Remote US", "US - Remote", "Remote, United States", "United States"])
    def test_us_remote_variants_resolve_country(self, raw):
        r = normalize_location(raw)
        assert r["location_country"] == "United States" and r["location_city"] is None

    def test_iso2_country_codes(self):
        assert normalize_location("Dublin, IE")["location_country"] == "Ireland"
        assert normalize_location("Sydney, AU")["location_country"] == "Australia"
        assert normalize_location("Zürich, CH")["location_country"] == "Switzerland"

    def test_regions_and_placeholders_are_not_cities(self):
        for raw in ("AMER", "N/A", "Remote", "EMEA - Remote"):
            assert normalize_location(raw)["location_city"] is None, raw

    def test_lowercase_two_letter_words_are_not_states(self):
        r = normalize_location("San Francisco or Palo Alto")
        assert r["location_state"] == "CA"          # not "OR" from the word "or"

    def test_city_is_clamped_to_column_width(self):
        r = normalize_location("Alabama; " + "; ".join(f"Office {i}" for i in range(40)))
        assert r["location_city"] is None or len(r["location_city"]) <= 100


# ── remote_type ────────────────────────────────────────────────────────

class TestRemoteType:
    def test_description_noise_does_not_make_an_office_job_remote(self):
        item = _enrich(location="Seattle, Washington, USA",
                       description_html="<p>You will build distributed systems and work with remote teams across time zones.</p>")
        assert item["remote_type"] == "unknown"
        assert item["is_remote_friendly"] == 0

    def test_location_remote_still_flips(self):
        assert _enrich(location="USA - Remote")["remote_type"] == "remote"
        assert _enrich(location="Remote (US)")["remote_type"] == "remote"

    def test_title_remote_flips(self):
        assert _enrich(location="", title="Backend Engineer (Remote)")["remote_type"] == "remote"

    def test_explicit_description_statement_counts(self):
        item = _enrich(location="Austin, TX", description_html="<p>This role is fully remote within the US.</p>")
        assert item["remote_type"] == "remote" and item["is_remote_friendly"] == 1

    def test_negated_statement_does_not_count(self):
        item = _enrich(location="Austin, TX", description_html="<p>This is not a remote position.</p>")
        assert item["remote_type"] == "unknown" and item["is_remote_friendly"] == 0

    def test_hybrid_location_is_hybrid(self):
        item = _enrich(location="Denver, CO - Hybrid; New York, NY - Hybrid",
                       description_html="<p>Remote-friendly culture, distributed team.</p>")
        assert item["remote_type"] == "hybrid"

    def test_ats_value_is_never_overridden(self):
        assert _enrich(location="Remote", remote_type="onsite")["remote_type"] == "onsite"


class TestAshbyWorkplaceType:
    URL = "https://api.ashbyhq.com/posting-api/job-board/acme"

    def test_workplace_type_beats_is_remote_flag(self, source):
        spider = AshbySpider()
        source["source_type"] = "ashby"
        jobs = {j["source_job_id"]: j for j in spider.parse_jobs(fake_response(self.URL, load_fixture("ashby_hybrid.json")), source)}
        assert jobs["hyb-1"]["remote_type"] == "hybrid"
        assert jobs["ons-1"]["remote_type"] == "onsite"
        assert jobs["rem-1"]["remote_type"] == "remote"     # no workplaceType → isRemote decides

    def test_hybrid_survives_the_enrich_pipeline(self, source):
        spider = AshbySpider()
        source["source_type"] = "ashby"
        items = list(spider.parse_jobs(fake_response(self.URL, load_fixture("ashby_hybrid.json")), source))
        out = [EnrichPipeline().process_item(NormalizePipeline().process_item(i, spider), spider) for i in items]
        by_id = {o["source_job_id"]: o for o in out}
        assert by_id["hyb-1"]["remote_type"] == "hybrid"
        assert by_id["hyb-1"]["location_city"] == "San Francisco" and by_id["hyb-1"]["location_state"] == "CA"
        assert by_id["ons-1"]["location_city"] == "New York" and by_id["ons-1"]["location_state"] == "NY"


# ── validator: US filter ───────────────────────────────────────────────

class TestUSFilter:
    @pytest.mark.parametrize("loc", [
        "Remote - United Kingdom", "Belgium - Remote", "UAE - Remote", "Oslo - Remote", "Remote - Denmark",
        "Remote - Sweden", "Sweden (Remote)", "Aarhus, Denmark", "Auckland, New Zealand", "Heredia, Costa Rica",
        "Finland", "Zürich, CH", "Dublin, IE", "Sydney, AU", "Ontario, CAN", "Ontario - Remote", "CAD - Toronto, Ontario - Remote",
        "DEU - Remote - Munich", "AUS-Remote", "IND-Remote", "Belgrade, Serbia", "Ljubljana, Slovenia", "Bengaluru",
        "São Paulo", "Vilnius, Lithuania", "Luxembourg", "Remote (Sweden)", "Markham, ON",
    ])
    def test_non_us_locations_are_dropped(self, loc):
        assert ValidatePipeline()._is_likely_us({"location": loc}) is False, loc

    @pytest.mark.parametrize("loc", [
        "Remote", "Remote - US", "USA - Remote", "Remote (US)", "United States", "Milwaukee, WI", "Milwaukee",
        "Indianapolis", "Remote - Indiana", "Athens, GA", "Vienna, VA", "Portland, OR", "Peru, IL", "Rome, Georgia",
        "Austin, TX", "Seattle, Washington, USA", "Denver, CO - Hybrid; Toronto, Ontario - Remote",
        "Seattle, San Francisco, New York, Chicago, Atlanta, Remote in the US, Canada",
    ])
    def test_us_locations_are_kept(self, loc):
        assert ValidatePipeline()._is_likely_us({"location": loc}) is True, loc


# ── validator: titles ──────────────────────────────────────────────────

class TestTitleFilter:
    def _drops(self, title):
        try:
            ValidatePipeline().process_item(_item(title=title), None)
        except DropItem as e:
            return "Non-IT" in str(e)
        return False

    @pytest.mark.parametrize("title", [
        "Account Executive | Enterprise", "Sales Development Representative", "Senior Tax Accountant",
        "Staff Accountant", "Payroll Analyst", "Recruiter, GTM", "Technical Recruiter", "Head of People Operations",
        "HR Business Partner", "Commercial Counsel, Enterprise", "Executive Assistant", "Strategic Finance Analyst",
        "Field Marketing Manager", "Brand Designer, Creative Studio", "Partner Account Manager", "Customer Success Manager",
        "Events Manager, Business", "Procurement Operations Manager", "Real Estate Portfolio Manager",
        "Radiologist", "Landscaper",
    ])
    def test_business_and_prefix_stem_titles_drop(self, title):
        assert self._drops(title), title

    @pytest.mark.parametrize("title", [
        "Sales Engineer", "Solutions Engineer, Enterprise", "Marketing Data Analyst", "Finance Systems Analyst",
        "Technical Account Manager", "Member of Technical Staff", "Site Reliability Engineer", "SRE",
        "Solutions Architect", "ML Researcher", "Data Analyst", "Research Scientist", "Applied Scientist",
        "HRIS Analyst (Workday)", "Security Operations Analyst", "Product Manager, Payments",
        "Software Engineer, Kitchen Display Systems", "Data Analyst, Nursing Operations", "Recruiting Platform Engineer",
        "Payroll Systems Engineer", "Salesforce Administrator", "Scrum Master", "Technical Writer",
    ])
    def test_tech_titles_are_kept(self, title):
        assert not self._drops(title), title

    def test_identity_columns_too_long_are_dropped_not_truncated(self):
        with pytest.raises(DropItem, match="apply_url too long"):
            ValidatePipeline().process_item(_item(apply_url="https://x.example/" + "a" * 500), None)
        with pytest.raises(DropItem, match="source_job_id too long"):
            ValidatePipeline().process_item(_item(source_job_id="j" * 101), None)


# ── seniority ──────────────────────────────────────────────────────────

class TestSeniority:
    @pytest.mark.parametrize("title,level", [
        ("Product Manager, Identity & Access Management", "mid"),      # function, not people-management
        ("Technical Program Manager", "mid"),
        ("Senior Product Manager - Technical", "senior"),
        ("Senior Manager, Account Manager | Mid-Market", "manager"),
        ("Senior Engineering Manager, Media Foundation", "manager"),
        ("Engineering Manager", "manager"),
        ("Manager, Product Management", "manager"),
        ("Product Manager Intern (PGC) - 2027 Summer", "intern"),
        ("Program Manager Intern", "intern"),
        ("Software Engineer, Internship - Sovereign AI Systems", "intern"),
        ("Software Development Engineer II, KMS", "mid"),
        ("Software Engineer I", "entry"),
        ("Business Intelligence Engineer III", "senior"),
        ("Software Engineer, Early Career", "entry"),
        ("Associate Product Manager, New Grad (2027 Start)", "entry"),
        ("Principal Product Designer, Design Systems Lead", "lead"),
        ("Staff Software Engineer", "lead"),
        ("Director, Product Management", "director"),
    ])
    def test_title_rules(self, title, level):
        assert infer_seniority(title)["seniority"] == level, title

    def test_first_stated_requirement_wins(self):
        desc = ("- 3+ years of non-internship professional software development experience\n"
                "- 1+ years of experience contributing to the architecture of new systems")
        r = infer_seniority("Software Development Engineer", desc)
        assert (r["years_experience_min"], r["seniority"]) == (3, "mid")

    def test_zero_to_two_years_is_entry_not_intern(self):
        assert infer_seniority("Fraud Analyst", "0-2 years of experience")["seniority"] == "entry"

    def test_years_never_imply_director(self):
        assert infer_seniority("Solutions Architect", "12+ years of experience")["seniority"] == "lead"

    def test_intern_title_fixes_full_time_employment_type(self):
        item = _enrich(title="Software Engineer, Internship - Warp Speed", employment_type="full_time")
        assert item["employment_type"] == "intern" and item["seniority"] == "intern"

    def test_intern_employment_type_fixes_seniority(self):
        item = _enrich(title="POI Content Product Manager Intern", employment_type="intern")
        assert item["seniority"] == "intern"


# ── skills ─────────────────────────────────────────────────────────────

class TestSkillFalsePositives:
    @pytest.mark.parametrize("text,skill", [
        ("We accelerate progress and spark innovation across teams.", "Spark"),
        ("Must be graduating in December 2027 or spring 2028.", "Spring"),
        ("Large-scale distributed training, including multi-node GPU training.", "Node.js"),
        ("Comfortable in tools like Figma, Canva, or Adobe Express.", "Express"),
        ("Ensuring swift resolution of customer issues; faster and cheaper vs. SWIFT and other fiat rails.", "Swift"),
        ("Build new products on crypto rails and payment rails.", "Rails"),
    ])
    def test_everyday_words_are_not_technologies(self, text, skill):
        assert skill not in extract_skills("Role", text)

    @pytest.mark.parametrize("text,skill", [
        ("Experience with Apache Spark and Kafka.", "Spark"),
        ("Python, SQL, and ideally Spark/PySpark.", "Spark"),
        ("Java (Spring Boot), Kubernetes.", "Spring"),
        ("Programming languages such as TypeScript, Node.js, or Python.", "Node.js"),
        ("Backend in Node/Express, frontend in React. Express.js APIs.", "Express"),
        ("Our iOS app is written in Swift and Objective-C.", "Swift"),
        ("Proficiency in Swift and experience developing iOS apps.", "Swift"),
        ("Ruby on Rails, React, GraphQL.", "Rails"),
    ])
    def test_real_mentions_still_count(self, text, skill):
        assert skill in extract_skills("Role", text)


# ── DB contract ────────────────────────────────────────────────────────

class TestSchemaConform:
    def test_booleans_become_0_1(self):
        item = _enrich(location="Austin, TX")
        for f in ("is_remote_friendly", "is_multi_cloud", "is_platform_heavy"):
            assert item[f] in (0, 1) and not isinstance(item[f], bool)

    def test_long_location_is_truncated_to_the_column(self):
        long_loc = "; ".join(["Alabama", "Chicago, Illinois", "Columbus, Ohio", "Connecticut", "Florida"] * 12)
        assert len(long_loc) > 300
        item = _enrich(location=long_loc)
        assert len(item["location"]) <= 300
        assert (item["location_city"], item["location_state"]) == ("Chicago", "IL")   # first City, State pair

    def test_enum_and_range_guards(self):
        d = {"seniority": "principal", "remote_type": "office", "employment_type": "temporary",
             "years_experience_min": 300, "years_experience_max": 5, "salary_min": "150,000", "salary_max": 120000.0,
             "quality_score": 101, "salary_currency": "US Dollar", "is_remote_friendly": True, "is_multi_cloud": None,
             "is_platform_heavy": False, "description_word_count": 12}
        notes = conform_item(d)
        assert d["seniority"] == d["remote_type"] == d["employment_type"] == "unknown"
        assert d["years_experience_min"] is None and d["years_experience_max"] == 5
        assert (d["salary_min"], d["salary_max"]) == (120000, 150000)     # coerced and swapped
        assert d["quality_score"] is None and d["salary_currency"] is None
        assert (d["is_remote_friendly"], d["is_multi_cloud"], d["is_platform_heavy"]) == (1, 0, 0)
        assert notes

    def test_clean_item_is_untouched(self):
        d = {"title": "SWE", "seniority": "mid", "remote_type": "remote", "employment_type": "full_time",
             "salary_min": 100000, "salary_max": 150000, "salary_currency": "usd",
             "is_remote_friendly": 1, "is_multi_cloud": 0, "is_platform_heavy": 0}
        assert conform_item(d) == [] and d["salary_currency"] == "USD"

    def test_payload_is_json_serialisable_ints(self):
        item = _enrich(location="Austin, TX")
        payload = json.loads(json.dumps(dict(item)))
        assert payload["is_remote_friendly"] == 0 and payload["seniority"] in ("mid", "entry", "senior")


class TestPostedAtClock:
    def test_aware_timestamps_are_converted_to_server_clock(self):
        assert BaseJobSpider.safe_datetime_iso("2026-11-07T03:00:00Z") == "2026-11-06 21:00:00"      # CST
        assert BaseJobSpider.safe_datetime_iso("2026-06-20T12:34:56-04:00") == "2026-06-20 11:34:56"  # CDT
        assert BaseJobSpider.safe_datetime_iso("2026-01-19T07:59:15-08:00") == "2026-01-19 09:59:15"

    def test_naive_values_pass_through(self):
        assert BaseJobSpider.safe_datetime_iso("2026-11-07T03:00:00") == "2026-11-07 03:00:00"
        assert BaseJobSpider.safe_datetime_iso("2026-11-07") == "2026-11-07 00:00:00"

    def test_epoch_ms_is_converted(self):
        assert BaseJobSpider.safe_datetime_epoch_ms(1750000000000) == "2025-06-15 10:06:40"

    def test_garbage_is_none(self):
        assert BaseJobSpider.safe_datetime_iso("Posted Yesterday") is None
        assert BaseJobSpider.safe_datetime_epoch_ms("x") is None
        assert BaseJobSpider.safe_datetime_epoch_ms(10 ** 22) is None

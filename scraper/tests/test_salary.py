"""Salary extraction from description text (formats seen in real postings)."""
from latentra_scraper.enrichment.salary import extract_salary
from latentra_scraper.pipelines.enrich import EnrichPipeline


def test_greenhouse_zone_table_with_entities():
    t = "Zone 1 Pay Range$201,800&mdash;$277,400 USDZone 2 Pay Range$181,600&mdash;$249,700 USD"
    assert extract_salary(t) == (201800, 277400)


def test_double_encoded_entities():
    assert extract_salary("Annual Salary:$270,000&amp;mdash;$315,000 USD") == (270000, 315000)


def test_amazon_usd_annually():
    t = "Learn more.\n\nUSA, WA, Seattle - 130,400.00 - 176,300.00 USD annually"
    assert extract_salary(t) == (130400, 176300)


def test_amazon_from_up_to():
    t = "base pay ranges from $129,300/year in our lowest geographic market up to $223,600/year in our highest"
    assert extract_salary(t) == (129300, 223600)


def test_k_and_to_and_nbsp():
    assert extract_salary("Salary: $150K to $210K") == (150000, 210000)
    assert extract_salary("Salary: $206,086.00&nbsp;- $285,600.00/yr") == (206086, 285600)


def test_rejects_implausible():
    assert extract_salary("$45 - $60 per hour") is None
    assert extract_salary("An estimated $124 trillion of assets") is None
    assert extract_salary("Amazon Ads is building a >$100BN business") is None
    assert extract_salary("$50,000 - $900,000") is None   # 18x spread: not a real band
    assert extract_salary("") is None and extract_salary(None) is None


def test_pipeline_fills_only_missing_salary():
    pipe = EnrichPipeline()
    base = {"title": "Data Engineer", "location": "Seattle, WA", "remote_type": "unknown",
            "description_text": "Pay: $140,000 - $180,000 USD annually range"}
    item = pipe.process_item(dict(base), None)
    assert (item["salary_min"], item["salary_max"], item["salary_currency"]) == (140000, 180000, "USD")
    given = pipe.process_item(dict(base, salary_min=100000, salary_max=120000, salary_currency="USD"), None)
    assert (given["salary_min"], given["salary_max"]) == (100000, 120000)   # ATS value wins

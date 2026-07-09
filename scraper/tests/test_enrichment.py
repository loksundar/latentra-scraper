"""Unit tests for the enrichment helpers (seniority, skills, location)."""

from latentra_scraper.enrichment.location import normalize_location
from latentra_scraper.enrichment.seniority import infer_seniority
from latentra_scraper.enrichment.skills import extract_skills


class TestSeniority:
    def test_title_wins(self):
        r = infer_seniority("Senior Software Engineer")
        assert r["seniority"] == "senior"
        assert r["seniority_confidence"] == "title"

    def test_specific_patterns_beat_generic(self):
        assert infer_seniority("VP of Engineering")["seniority"] == "vp"
        assert infer_seniority("Director, Data Platform")["seniority"] == "director"
        assert infer_seniority("Staff Engineer")["seniority"] == "lead"
        assert infer_seniority("Engineering Manager")["seniority"] == "manager"
        assert infer_seniority("Software Engineering Intern")["seniority"] == "intern"

    def test_years_from_description_when_title_generic(self):
        r = infer_seniority("Software Engineer", "You have 5+ years of experience building APIs.")
        assert r["seniority"] == "senior"
        assert r["seniority_confidence"] == "years"
        assert r["years_experience_min"] == 5

    def test_years_range(self):
        r = infer_seniority("Software Engineer", "3-5 years of experience required.")
        assert r["years_experience_min"] == 3
        assert r["years_experience_max"] == 5

    def test_title_not_overridden_by_years(self):
        r = infer_seniority("Junior Developer", "10+ years leading teams (of our founders).")
        assert r["seniority"] == "entry"
        assert r["seniority_confidence"] == "title"

    def test_default_is_mid(self):
        r = infer_seniority("Software Engineer")
        assert r["seniority"] == "mid"
        assert r["seniority_confidence"] == "default"


class TestSkills:
    def test_extracts_from_title_and_description(self):
        skills = extract_skills(
            "Senior Python Engineer",
            "Experience with AWS, Kubernetes and PostgreSQL required.",
        )
        # Skill names are display-cased
        assert "Python" in skills
        assert "AWS" in skills
        assert "Kubernetes" in skills

    def test_deduplicated_and_sorted(self):
        skills = extract_skills("Python Developer", "python, Python, PYTHON")
        assert skills.count("Python") == 1
        assert skills == sorted(skills)

    def test_empty_input(self):
        assert extract_skills("") == []


class TestLocation:
    def test_us_city_state(self):
        r = normalize_location("San Francisco, CA")
        assert r["location_city"] == "San Francisco"
        assert r["location_state"] == "CA"
        assert r["location_country"] == "United States"

    def test_remote_flag_from_location(self):
        r = normalize_location("Remote - US")
        assert r["is_remote_friendly"] is True

    def test_remote_flag_from_title(self):
        r = normalize_location("", title="Backend Engineer (Remote)")
        assert r["is_remote_friendly"] is True

    def test_empty_location(self):
        r = normalize_location("")
        assert r["location_city"] is None
        assert r["location_country"] is None

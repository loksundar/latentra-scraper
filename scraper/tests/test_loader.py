"""Tests for the sources loader's SOURCES_FILE mode (used in CI)."""

import json

from latentra_scraper.sources import loader


def test_sources_file_mode(tmp_path, monkeypatch):
    data = {
        "greenhouse": [
            {"source_type": "greenhouse", "company_slug": "acme",
             "company_name": "Acme", "base_url": None}
        ],
        "lever": [],
    }
    f = tmp_path / "sources.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("SOURCES_FILE", str(f))

    assert loader.get_active_sources("greenhouse") == data["greenhouse"]
    assert loader.get_active_sources("lever") == []
    # Types absent from the file yield empty, not an error
    assert loader.get_active_sources("workday") == []


def test_source_types_cover_all_spiders():
    assert set(loader.SOURCE_TYPES) == {
        "greenhouse", "lever", "workday", "ashby", "smartrecruiters"
    }

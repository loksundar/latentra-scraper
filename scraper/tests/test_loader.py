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


def test_shards_partition_sources(tmp_path, monkeypatch):
    rows = [{"source_type": "workday", "company_slug": f"co{i}"} for i in range(50)]
    f = tmp_path / "sources.json"
    f.write_text(json.dumps({"workday": rows}), encoding="utf-8")
    monkeypatch.setenv("SOURCES_FILE", str(f))
    monkeypatch.setenv("SHARD_TOTAL", "4")
    seen = []
    for i in range(4):
        monkeypatch.setenv("SHARD_INDEX", str(i))
        seen += [s["company_slug"] for s in loader.get_active_sources("workday")]
    # every company crawled exactly once across shards
    assert sorted(seen) == sorted(r["company_slug"] for r in rows)


def test_no_shard_env_returns_all(tmp_path, monkeypatch):
    rows = [{"source_type": "lever", "company_slug": "a"}, {"source_type": "lever", "company_slug": "b"}]
    f = tmp_path / "sources.json"
    f.write_text(json.dumps({"lever": rows}), encoding="utf-8")
    monkeypatch.setenv("SOURCES_FILE", str(f))
    monkeypatch.delenv("SHARD_TOTAL", raising=False)
    assert loader.get_active_sources("lever") == rows

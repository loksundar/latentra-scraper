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
        "greenhouse", "lever", "workday", "ashby", "smartrecruiters",
        "workable", "rippling", "gem", "oracle",
        "amazon", "apple", "tiktok",
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


def test_duplicate_tenants_are_crawled_once(tmp_path, monkeypatch, caplog):
    """bah + booz-allen (bah/bah_jobs vs bah/BAH_Jobs) and motorola +
    motorolasolutions point at one Workday tenant: same jobReqIds, and
    jobs.uq_source_job has no company_slug, so two slugs fight over one row."""
    data = {"workday": [
        {"source_type": "workday", "company_slug": "bah", "base_url": "https://bah.wd1.myworkdayjobs.com/wday/cxs/bah/bah_jobs"},
        {"source_type": "workday", "company_slug": "booz-allen", "base_url": "https://bah.wd1.myworkdayjobs.com/wday/cxs/bah/BAH_Jobs/"},
        {"source_type": "workday", "company_slug": "motorola", "base_url": "https://motorolasolutions.wd5.myworkdayjobs.com/wday/cxs/motorolasolutions/Careers"},
        {"source_type": "workday", "company_slug": "motorolasolutions", "base_url": "https://motorolasolutions.wd5.myworkdayjobs.com/wday/cxs/motorolasolutions/Careers"},
        {"source_type": "workday", "company_slug": "nvidia", "base_url": "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite"},
        {"source_type": "greenhouse", "company_slug": "no-base-url-a", "base_url": None},
        {"source_type": "greenhouse", "company_slug": "no-base-url-b", "base_url": None},
    ]}
    f = tmp_path / "sources.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("SOURCES_FILE", str(f))
    monkeypatch.delenv("SHARD_TOTAL", raising=False)

    kept = [s["company_slug"] for s in loader.get_active_sources("workday")]
    assert kept == ["bah", "motorola", "nvidia", "no-base-url-a", "no-base-url-b"]
    assert "booz-allen has the same base_url as bah" in caplog.text
    assert "motorolasolutions has the same base_url as motorola" in caplog.text

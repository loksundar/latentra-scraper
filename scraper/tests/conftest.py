"""Shared helpers for spider golden-file tests."""

import os

import pytest
from scrapy.http import Request, TextResponse

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def load_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as f:
        return f.read()


def fake_response(url: str, body: str) -> TextResponse:
    return TextResponse(
        url=url,
        body=body.encode("utf-8"),
        encoding="utf-8",
        request=Request(url=url),
    )


@pytest.fixture
def source():
    """A generic job_sources row as returned by the sources API."""
    return {
        "source_type": "",
        "company_slug": "acme",
        "company_name": "Acme",
        "base_url": None,
    }

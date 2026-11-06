"""
Source validator — checks if a classified source actually returns jobs.
Makes a lightweight API call to verify the source is live.
"""

import json
import logging

import requests

logger = logging.getLogger(__name__)

# Timeout for validation requests
TIMEOUT = 15

# Several ATS front doors (Workable, Rippling, Oracle) 403 non-browser UAs.
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
       "Accept": "application/json"}


def validate_source(source: dict) -> dict:
    """
    Validate a classified source by hitting its API.
    Returns: {"valid": bool, "job_count": int|None, "company_name": str|None, "error": str|None}
    """
    source_type = source["source_type"]
    slug = source["company_slug"]
    base_url = source.get("base_url")

    try:
        if source_type == "greenhouse":
            return _validate_greenhouse(slug)
        elif source_type == "lever":
            return _validate_lever(slug)
        elif source_type == "ashby":
            return _validate_ashby(slug)
        elif source_type == "workday":
            return _validate_workday(base_url, slug)
        elif source_type == "smartrecruiters":
            return _validate_smartrecruiters(slug)
        elif source_type == "workable":
            return _validate_workable(slug)
        elif source_type == "rippling":
            return _validate_rippling(slug)
        elif source_type == "gem":
            return _validate_gem(slug)
        elif source_type == "oracle":
            return _validate_oracle(base_url)
        else:
            return {"valid": False, "error": f"Unknown source_type: {source_type}"}
    except Exception as e:
        return {"valid": False, "error": str(e)}


def _validate_greenhouse(slug: str) -> dict:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    r = requests.get(url, timeout=TIMEOUT)
    if r.status_code != 200:
        return {"valid": False, "error": f"HTTP {r.status_code}"}
    data = r.json()
    count = len(data.get("jobs", []))
    return {"valid": count > 0, "job_count": count}


def _validate_lever(slug: str) -> dict:
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    r = requests.get(url, timeout=TIMEOUT)
    if r.status_code != 200:
        return {"valid": False, "error": f"HTTP {r.status_code}"}
    jobs = r.json()
    if not isinstance(jobs, list):
        return {"valid": False, "error": "Unexpected response"}
    return {"valid": len(jobs) > 0, "job_count": len(jobs)}


def _validate_ashby(slug: str) -> dict:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    r = requests.get(url, timeout=TIMEOUT)
    if r.status_code != 200:
        return {"valid": False, "error": f"HTTP {r.status_code}"}
    data = r.json()
    jobs = data.get("jobs", [])
    return {"valid": len(jobs) > 0, "job_count": len(jobs)}


def _validate_workday(base_url: str, slug: str) -> dict:
    if not base_url:
        return {"valid": False, "error": "Workday requires base_url"}
    url = base_url.rstrip("/") + "/jobs"
    r = requests.post(
        url,
        json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        return {"valid": False, "error": f"HTTP {r.status_code}"}
    data = r.json()
    total = data.get("total", 0)
    return {"valid": total > 0, "job_count": total}


def _validate_smartrecruiters(slug: str) -> dict:
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    r = requests.get(url, params={"limit": 1}, timeout=TIMEOUT)
    if r.status_code != 200:
        return {"valid": False, "error": f"HTTP {r.status_code}"}
    total = r.json().get("totalFound", 0)
    return {"valid": total > 0, "job_count": total}


def _validate_workable(slug: str) -> dict:
    r = requests.get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}", headers=_UA, timeout=TIMEOUT)
    if r.status_code != 200:
        return {"valid": False, "error": f"HTTP {r.status_code}"}
    data = r.json()
    jobs = data.get("jobs") or []
    return {"valid": len(jobs) > 0, "job_count": len(jobs), "company_name": data.get("name")}


def _validate_rippling(slug: str) -> dict:
    r = requests.get(f"https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs", headers=_UA, timeout=TIMEOUT)
    if r.status_code != 200:
        return {"valid": False, "error": f"HTTP {r.status_code}"}
    jobs = r.json()
    if not isinstance(jobs, list):
        return {"valid": False, "error": "Unexpected response"}
    return {"valid": len(jobs) > 0, "job_count": len(jobs)}


def _validate_gem(slug: str) -> dict:
    r = requests.get(f"https://api.gem.com/job_board/v0/{slug}/job_posts/", headers=_UA, timeout=TIMEOUT)
    if r.status_code != 200:
        return {"valid": False, "error": f"HTTP {r.status_code}"}
    jobs = r.json()
    if isinstance(jobs, dict):
        jobs = jobs.get("job_posts") or jobs.get("jobs") or []
    return {"valid": len(jobs) > 0, "job_count": len(jobs)}


def _validate_oracle(base_url: str) -> dict:
    from latentra_scraper.spiders.oracle import parse_base_url
    host, site = parse_base_url(base_url)
    if not host:
        return {"valid": False, "error": "Oracle requires base_url"}
    url = (f"{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
           f"?onlyData=true&finder=findReqs;siteNumber={site},limit=1,offset=0")
    r = requests.get(url, headers=_UA, timeout=TIMEOUT)
    if r.status_code != 200:
        return {"valid": False, "error": f"HTTP {r.status_code}"}
    items = r.json().get("items") or []
    total = items[0].get("TotalJobsCount", 0) if items else 0
    return {"valid": total > 0, "job_count": total}

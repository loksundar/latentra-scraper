"""
Platform keyword extraction — detects cloud/data platform mentions in job descriptions.
Supports multi-cloud and platform-heavy role detection.
"""

import re

# Platform keywords grouped by category
# Each: (canonical_name, [regex_patterns])
PLATFORM_KEYWORDS = {
    # Cloud providers
    "AWS": [r"\baws\b", r"\bamazon web services\b"],
    "GCP": [r"\bgcp\b", r"\bgoogle cloud\b(?:\s+platform)?"],
    "Azure": [r"\bazure\b", r"\bmicrosoft azure\b"],
    # Data platforms
    "Databricks": [r"\bdatabricks\b"],
    "Snowflake": [r"\bsnowflake\b"],
    "BigQuery": [r"\bbigquery\b", r"\bbig\s+query\b"],
    "Redshift": [r"\bredshift\b"],
    # Data processing
    "Spark": [r"\bspark\b", r"\bpyspark\b", r"\bapache\s+spark\b"],
    "Airflow": [r"\bairflow\b"],
    "Kafka": [r"\bkafka\b"],
    "Flink": [r"\bflink\b"],
    "Hadoop": [r"\bhadoop\b"],
    # Infra / DevOps
    "Kubernetes": [r"\bkubernetes\b", r"\bk8s\b"],
    "Docker": [r"\bdocker\b"],
    "Terraform": [r"\bterraform\b"],
}

# Cloud providers for multi-cloud detection
CLOUD_PROVIDERS = {"AWS", "GCP", "Azure"}

# All platform names for platform-heavy detection
ALL_PLATFORMS = set(PLATFORM_KEYWORDS.keys())

# Pre-compile
_COMPILED: list[tuple[str, list[re.Pattern]]] = [
    (name, [re.compile(p, re.IGNORECASE) for p in patterns])
    for name, patterns in PLATFORM_KEYWORDS.items()
]

# Threshold for platform-heavy role
PLATFORM_HEAVY_THRESHOLD = 6
# Min mentions per platform for multi-cloud
MULTI_CLOUD_MIN_MENTIONS = 2
# Min distinct cloud providers for multi-cloud
MULTI_CLOUD_MIN_PROVIDERS = 2


def extract_platform_keywords(description_text: str) -> dict:
    """
    Count platform keyword occurrences in description text.

    Returns:
        {
            "platform_keywords": {"AWS": 3, "Azure": 2, ...},
            "primary_cloud_platforms": ["AWS", "Azure"],
            "is_multi_cloud": bool,
            "is_platform_heavy": bool,
        }
    """
    if not description_text:
        return {
            "platform_keywords": {},
            "primary_cloud_platforms": [],
            "is_multi_cloud": False,
            "is_platform_heavy": False,
        }

    text = description_text

    # Count occurrences per platform
    counts = {}
    for name, patterns in _COMPILED:
        total = 0
        for pattern in patterns:
            total += len(pattern.findall(text))
        if total > 0:
            counts[name] = total

    # Determine primary cloud platforms (cloud providers with 1+ mentions)
    primary_clouds = sorted(
        [name for name in CLOUD_PROVIDERS if counts.get(name, 0) > 0],
        key=lambda n: counts[n],
        reverse=True,
    )

    # Multi-cloud: 2+ cloud providers each mentioned 2+ times
    qualifying_clouds = [
        name for name in CLOUD_PROVIDERS
        if counts.get(name, 0) >= MULTI_CLOUD_MIN_MENTIONS
    ]
    is_multi_cloud = len(qualifying_clouds) >= MULTI_CLOUD_MIN_PROVIDERS

    # Platform-heavy: total platform mentions exceed threshold
    total_mentions = sum(counts.values())
    is_platform_heavy = total_mentions >= PLATFORM_HEAVY_THRESHOLD

    return {
        "platform_keywords": counts,
        "primary_cloud_platforms": primary_clouds,
        "is_multi_cloud": is_multi_cloud,
        "is_platform_heavy": is_platform_heavy,
    }

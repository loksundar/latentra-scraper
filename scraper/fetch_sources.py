"""
Fetch every source list from the API once and write them to a JSON file.

Run in CI before the spider matrix; spiders then read the file via the
SOURCES_FILE env var instead of five parallel jobs hitting the sources API
at the same instant (which trips Hostinger's WAF into 403s).

Usage:
    python fetch_sources.py [output.json]
"""

import json
import sys
import time

from latentra_scraper.sources.loader import SOURCE_TYPES, get_active_sources


def main() -> int:
    out_path = sys.argv[1] if len(sys.argv) > 1 else "sources.json"

    data = {}
    for i, source_type in enumerate(SOURCE_TYPES):
        if i:
            time.sleep(2)  # be gentle — sequential, spaced requests
        data[source_type] = get_active_sources(source_type)

    total = sum(len(v) for v in data.values())
    if total == 0:
        print("ERROR: zero sources loaded across all types", file=sys.stderr)
        return 1

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    counts = ", ".join(f"{t}={len(v)}" for t, v in data.items())
    print(f"Wrote {total} sources to {out_path} ({counts})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

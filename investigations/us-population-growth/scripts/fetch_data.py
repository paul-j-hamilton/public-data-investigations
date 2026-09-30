#!/usr/bin/env python3
"""Fetch and preserve the World Bank response used by this investigation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

API_URL = (
    "https://api.worldbank.org/v2/country/USA/indicator/SP.POP.TOTL"
    "?format=json&per_page=100"
)
ARTICLE_DIR = Path(__file__).resolve().parents[1]
RAW_PATH = ARTICLE_DIR / "data" / "raw" / "world-bank-us-population.json"
MANIFEST_PATH = ARTICLE_DIR / "data" / "raw" / "manifest.json"


def main() -> None:
    request = Request(API_URL, headers={"User-Agent": "public-data-investigation/1.0"})
    with urlopen(request, timeout=60) as response:
        payload = response.read()

    # Refuse to preserve an error page or a structurally unexpected response.
    parsed = json.loads(payload)
    if not isinstance(parsed, list) or len(parsed) != 2 or not isinstance(parsed[1], list):
        raise ValueError("The World Bank response did not have the expected two-part structure")

    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_PATH.write_bytes(payload)

    manifest = {
        "source_url": API_URL,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "records": len(parsed[1]),
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Preserved {manifest['records']} records at {RAW_PATH}")
    print(f"SHA-256: {manifest['sha256']}")


if __name__ == "__main__":
    main()


#!/usr/bin/env python3
"""Fetch and preserve the OECD responses used by this investigation.

This is the ONLY script in the investigation that touches the network. It makes
two requests to the OECD's public SDMX API (no key or login is required):

1. DATA: every annual observation for six MSTI measures (researchers, R&D
   personnel, the three sector breakdowns of researchers, and total
   employment), in every unit the OECD publishes them in, for every country
   and year available.
2. STRUCTURE: the dataflow's metadata, which contains the human-readable labels
   for every code in the data (country names, what "E" or "B" means in the
   status columns, and so on).

Both responses are written to data/raw/ byte-for-byte, exactly as the OECD sent
them, and a manifest records when they were retrieved and a SHA-256 checksum of
each file. Anyone can re-run this script later and compare checksums to see
whether the OECD has revised the underlying data.

Run it only when you deliberately want a new snapshot:

    python3 investigations/oecd-rd-workforce/scripts/fetch_data.py
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

# --- snippet: request ---
# The OECD SDMX API identifies a dataset by "agency,dataflow,version".
# MSTI = Main Science and Technology Indicators. An empty version means "latest".
DATAFLOW = "OECD.STI.STP,DSD_MSTI@DF_MSTI,"

# An SDMX "key" selects values for each dimension, in order, separated by dots:
#   REF_AREA . FREQ . MEASURE . UNIT_MEASURE . PRICE_BASE . TRANSFORMATION
# An empty position means "all values". "+" joins several values.
# So this key asks for: all countries, annual data, six measures, all units,
# all price bases, and all transformations. We filter further in analyze.py,
# in the open, rather than silently here.
MEASURES = [
    "T_RS",     # Researchers (all sectors)
    "T_TT",     # R&D personnel: researchers + technicians + support staff
    "B_RS",     # Researchers in the business enterprise sector
    "G_RS",     # Researchers in the government sector
    "H_RS",     # Researchers in the higher education sector
    "TOT_EMP",  # Total employment: the denominator for "share of workforce"
]
DATA_KEY = ".A." + "+".join(MEASURES) + "..."

DATA_URL = (
    f"https://sdmx.oecd.org/public/rest/data/{DATAFLOW}/{DATA_KEY}"
    # AllDimensions returns one flat row per observation, which is easiest to audit.
    "?dimensionAtObservation=AllDimensions"
)
STRUCTURE_URL = (
    "https://sdmx.oecd.org/public/rest/dataflow/OECD.STI.STP/DSD_MSTI@DF_MSTI/latest"
    # references=all bundles the codelists (labels) together with the dataflow.
    "?references=all"
)

# The Accept header chooses the response format: CSV for data, JSON for metadata.
DATA_ACCEPT = "application/vnd.sdmx.data+csv; charset=utf-8"
STRUCTURE_ACCEPT = "application/vnd.sdmx.structure+json; charset=utf-8; version=1.0"
# --- end snippet: request ---

ARTICLE_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = ARTICLE_DIR / "data" / "raw"
DATA_PATH = RAW_DIR / "oecd-msti-rd-personnel.csv"
STRUCTURE_PATH = RAW_DIR / "oecd-msti-structure.json"
MANIFEST_PATH = RAW_DIR / "manifest.json"


def download(url: str, accept: str) -> bytes:
    """Return the raw bytes of an HTTP GET response."""
    request = Request(
        url,
        headers={"Accept": accept, "User-Agent": "public-data-investigation/1.0"},
    )
    with urlopen(request, timeout=180) as response:
        return response.read()


# --- snippet: sanity-checks ---
def check_data(payload: bytes) -> int:
    """Refuse to save an error page or a response with an unexpected shape.

    Returns the number of observation rows so it can be recorded in the manifest.
    """
    rows = list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
    if not rows:
        raise ValueError("The OECD data response contained no rows")

    required = {"REF_AREA", "MEASURE", "UNIT_MEASURE", "TIME_PERIOD", "OBS_VALUE"}
    missing = required - set(rows[0])
    if missing:
        raise ValueError(f"The OECD data response is missing columns: {sorted(missing)}")

    # Every measure we asked for should come back; if one vanishes, stop and look.
    returned = {row["MEASURE"] for row in rows}
    absent = set(MEASURES) - returned
    if absent:
        raise ValueError(f"The OECD data response is missing measures: {sorted(absent)}")
    return len(rows)


def check_structure(payload: bytes) -> None:
    """Confirm the metadata response contains codelists we can read labels from."""
    structure = json.loads(payload)
    if not structure.get("data", {}).get("codelists"):
        raise ValueError("The OECD structure response contained no codelists")
# --- end snippet: sanity-checks ---


def main() -> None:
    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    data = download(DATA_URL, DATA_ACCEPT)
    records = check_data(data)
    structure = download(STRUCTURE_URL, STRUCTURE_ACCEPT)
    check_structure(structure)

    # Only write files after BOTH responses pass their checks, so a failed
    # refresh never leaves a half-updated snapshot behind.
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_bytes(data)
    STRUCTURE_PATH.write_bytes(structure)

    manifest = {
        "retrieved_at_utc": retrieved_at,
        "files": [
            {
                "path": DATA_PATH.name,
                "source_url": DATA_URL,
                "accept_header": DATA_ACCEPT,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "records": records,
            },
            {
                "path": STRUCTURE_PATH.name,
                "source_url": STRUCTURE_URL,
                "accept_header": STRUCTURE_ACCEPT,
                "sha256": hashlib.sha256(structure).hexdigest(),
                "bytes": len(structure),
            },
        ],
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Preserved {records} observations at {DATA_PATH}")
    print(f"Preserved structure metadata at {STRUCTURE_PATH}")


if __name__ == "__main__":
    main()

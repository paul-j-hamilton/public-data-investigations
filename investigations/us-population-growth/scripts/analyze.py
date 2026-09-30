#!/usr/bin/env python3
"""Create the analysis-ready population series from the preserved raw snapshot."""

from __future__ import annotations

import csv
import json
from pathlib import Path

ARTICLE_DIR = Path(__file__).resolve().parents[1]
RAW_PATH = ARTICLE_DIR / "data" / "raw" / "world-bank-us-population.json"
OUTPUT_PATH = ARTICLE_DIR / "data" / "processed" / "us_population.csv"
EXPECTED_INDICATOR = "SP.POP.TOTL"


def load_observations() -> list[dict[str, object]]:
    response = json.loads(RAW_PATH.read_text(encoding="utf-8"))
    if not isinstance(response, list) or len(response) != 2 or not isinstance(response[1], list):
        raise ValueError("Unexpected World Bank response structure")

    observations = []
    for row in response[1]:
        indicator = row.get("indicator", {}).get("id")
        if indicator != EXPECTED_INDICATOR:
            raise ValueError(f"Unexpected indicator: {indicator!r}")
        if row.get("value") is None:
            continue
        observations.append(
            {
                "year": int(row["date"]),
                "population": int(row["value"]),
                "country_code": row["countryiso3code"],
                "indicator": indicator,
            }
        )

    observations.sort(key=lambda row: row["year"])
    if not observations:
        raise ValueError("No populated observations were found")
    return observations


def add_growth(observations: list[dict[str, object]]) -> None:
    previous = None
    for row in observations:
        current = int(row["population"])
        row["annual_growth_pct"] = (
            "" if previous is None else round((current / previous - 1) * 100, 4)
        )
        previous = current


def write_csv(observations: list[dict[str, object]]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fields = ["year", "population", "annual_growth_pct", "country_code", "indicator"]
    with OUTPUT_PATH.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(observations)


def main() -> None:
    observations = load_observations()
    add_growth(observations)
    write_csv(observations)
    print(f"Wrote {len(observations)} observations to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()


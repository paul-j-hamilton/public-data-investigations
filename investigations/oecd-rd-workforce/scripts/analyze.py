#!/usr/bin/env python3
"""Turn the preserved OECD snapshot into the analysis-ready tables used in the article.

This script never uses the network. It reads only the two files in data/raw/
(saved by fetch_data.py) and writes four CSV files to data/processed/:

- researchers_timeseries.csv  every country-year with researchers and R&D
                              personnel per 1,000 employed, plus status flags
- latest_by_country.csv       each country's most recent value (the rankings)
- sector_shares_latest.csv    where each country's researchers work: business,
                              government, or higher education
- ratio_check.csv             our independent recomputation of the OECD's
                              "per 1,000 employed" figures from raw counts

Every analytical decision is a named constant or a short, commented function
below, and the article shows each piece next to the result it produces. Run:

    python3 investigations/oecd-rd-workforce/scripts/analyze.py

It uses only the Python standard library, so there is nothing to install.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

ARTICLE_DIR = Path(__file__).resolve().parents[1]
RAW_DATA = ARTICLE_DIR / "data" / "raw" / "oecd-msti-rd-personnel.csv"
RAW_STRUCTURE = ARTICLE_DIR / "data" / "raw" / "oecd-msti-structure.json"
OUT_DIR = ARTICLE_DIR / "data" / "processed"

# --- snippet: decisions ---
# DECISION 1: Which years count as "current"?
# Countries report on different schedules. A country whose latest figure is
# many years old would be ranked against others' recent figures, which is
# misleading in a field that has grown quickly. We keep a country in the
# current rankings only if its latest value is within this many years of the
# most recent year anyone reports. Stale countries are listed, not hidden.
CURRENT_WINDOW_YEARS = 5

# DECISION 2: Which rows are regional aggregates rather than countries?
# They are kept in the data for comparison but drawn differently in charts
# and never counted as a "country" in the text.
AGGREGATES = {"OECD", "EU27_2020"}

# DECISION 3: How closely must our recomputation match the OECD's figure?
# The OECD publishes the ratio to ~10 significant digits, so any real
# discrepancy would be far larger than one part in a million.
RATIO_TOLERANCE = 1e-6
# --- end snippet: decisions ---


# --- snippet: labels ---
def load_labels() -> dict[str, dict[str, str]]:
    """Read human-readable names for every code from the OECD's own metadata.

    The data file only contains codes such as "KOR" or "E". The structure file
    maps each code to its label (e.g. "Korea", "Estimated value"). We use the
    OECD's labels rather than typing our own so they cannot drift or be misspelled.
    Returns {codelist id: {code: label}}, e.g. labels["CL_AREA"]["KOR"] == "Korea".
    """
    structure = json.loads(RAW_STRUCTURE.read_text(encoding="utf-8"))
    labels = {}
    for codelist in structure["data"]["codelists"]:
        labels[codelist["id"]] = {code["id"]: code["name"] for code in codelist["codes"]}
    return labels
# --- end snippet: labels ---


# --- snippet: load ---
def load_observations() -> dict[tuple[str, str, str, int], dict[str, str]]:
    """Read the raw OECD CSV and index the rows we need.

    The raw file contains every unit and transformation the OECD publishes for
    our six measures, including year-over-year growth rates. We keep only the
    levels (TRANSFORMATION "_Z" = "not applicable", i.e. no transformation) and
    only rows that actually contain a number.

    Returns a dictionary keyed by (country, measure, unit, year) so that later
    steps can look up, say, Korea's researchers in FTE for 2024 directly.
    """
    with RAW_DATA.open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))

    indexed = {}
    for row in rows:
        if row["TRANSFORMATION"] != "_Z":   # drop growth-rate and index rows
            continue
        if row["OBS_VALUE"] == "":          # drop empty cells (no value reported)
            continue
        if row["FREQ"] != "A":              # we asked for annual; enforce it
            raise ValueError(f"Unexpected frequency {row['FREQ']!r}")

        key = (row["REF_AREA"], row["MEASURE"], row["UNIT_MEASURE"], int(row["TIME_PERIOD"]))
        if key in indexed:
            # Two values for the same country/measure/unit/year would make the
            # result depend on file order. Stop rather than silently pick one.
            raise ValueError(f"Duplicate observation for {key}")
        indexed[key] = row
    return indexed


def value(row: dict[str, str]) -> float:
    """Return an observation's numeric value in plain units.

    UNIT_MULT is a power of ten: total employment is published in thousands
    (UNIT_MULT = 3), so 103399 means 103,399,000 people. Everything else in
    this investigation has UNIT_MULT = 0.
    """
    return float(row["OBS_VALUE"]) * 10 ** int(row["UNIT_MULT"] or 0)
# --- end snippet: load ---


# --- snippet: flags ---
def describe_flags(row: dict[str, str], labels: dict[str, dict[str, str]]) -> str:
    """Translate an observation's status codes into readable words.

    The OECD attaches up to three status codes (e.g. "E" = estimated,
    "B" = break in series, "P" = provisional, "D" = definition differs) plus an
    auxiliary code (e.g. "M2" = underestimated). We keep all of them, in words,
    so readers can see them in chart tooltips and the downloadable tables.
    """
    notes = []
    for column in ("OBS_STATUS", "OBS_STATUS_2", "OBS_STATUS_3"):
        code = row.get(column, "")
        if code and code != "A":  # "A" means "normal value": nothing to report
            notes.append(labels["CL_OBS_STATUS"].get(code, code))
    aux = row.get("AUX_OBS_STATUS", "")
    if aux:
        notes.append(labels["CL_AUX_OBS_STATUS"].get(aux, aux))
    # Remove repeats while keeping order (the same code can appear twice).
    return "; ".join(dict.fromkeys(notes))


def has_break(row: dict[str, str]) -> bool:
    """True if the OECD marks this year as a break in the series.

    A break means the method or definition changed, so the jump between this
    year and the previous one may be partly an artefact of measurement.
    """
    codes = {row.get(c, "") for c in ("OBS_STATUS", "OBS_STATUS_2", "OBS_STATUS_3")}
    return "B" in codes or row.get("AUX_OBS_STATUS") == "A2"
# --- end snippet: flags ---


# --- snippet: verify ---
def verify_ratios(obs) -> list[dict[str, object]]:
    """Independently recompute every "per 1,000 employed" figure.

    The OECD publishes researchers per 1,000 employed as a ready-made ratio.
    Rather than take it on trust, we rebuild it from its two ingredients, both
    in the same snapshot:

        full-time-equivalent researchers / total employment (persons) x 1,000

    If any recomputed value differs from the published one by more than
    RATIO_TOLERANCE, the script stops. The full comparison is saved so anyone
    can inspect it row by row.
    """
    checks = []
    for (area, measure, unit, year), published in obs.items():
        if unit != "10P3EMP":
            continue
        fte = obs.get((area, measure, "FTE", year))
        employment = obs.get((area, "TOT_EMP", "PS", year))
        if fte is None or employment is None:
            raise ValueError(f"Cannot verify {area} {measure} {year}: an ingredient is missing")

        recomputed = value(fte) / value(employment) * 1000
        relative_difference = abs(recomputed / value(published) - 1)
        if relative_difference > RATIO_TOLERANCE:
            raise ValueError(
                f"{area} {measure} {year}: OECD publishes {value(published)}, "
                f"but FTE / employment x 1000 = {recomputed}"
            )
        checks.append({
            "country_code": area,
            "measure": measure,
            "year": year,
            "published_per_1000": value(published),
            "fte": value(fte),
            "total_employment": value(employment),
            "recomputed_per_1000": round(recomputed, 9),
            "relative_difference": f"{relative_difference:.2e}",
        })
    return sorted(checks, key=lambda r: (r["country_code"], r["measure"], r["year"]))
# --- end snippet: verify ---


# --- snippet: timeseries ---
def build_timeseries(obs, labels) -> list[dict[str, object]]:
    """One row per country-year with both headline ratios side by side.

    - researchers_per_1000:  T_RS, researchers only
    - rd_personnel_per_1000: T_TT, researchers + technicians + support staff
    A year appears if at least one of the two ratios is published for it; the
    other is left blank rather than filled in or interpolated.
    """
    country_years = {
        (area, year)
        for (area, measure, unit, year) in obs
        if unit == "10P3EMP" and measure in ("T_RS", "T_TT")
    }
    table = []
    for area, year in sorted(country_years):
        researchers = obs.get((area, "T_RS", "10P3EMP", year))
        personnel = obs.get((area, "T_TT", "10P3EMP", year))
        table.append({
            "country_code": area,
            "country": labels["CL_AREA"].get(area, area),
            "is_aggregate": area in AGGREGATES,
            "year": year,
            "researchers_per_1000": round(value(researchers), 4) if researchers else "",
            "rd_personnel_per_1000": round(value(personnel), 4) if personnel else "",
            "researchers_flags": describe_flags(researchers, labels) if researchers else "",
            "rd_personnel_flags": describe_flags(personnel, labels) if personnel else "",
            "researchers_break": has_break(researchers) if researchers else "",
        })
    return table
# --- end snippet: timeseries ---


# --- snippet: latest ---
def build_latest(timeseries) -> list[dict[str, object]]:
    """Each country's most recent value for each ratio, with a staleness flag.

    The two ratios are treated separately: a country's latest researchers
    figure and latest R&D-personnel figure may come from different years, so
    each gets its own year column. The "*_is_current" columns apply DECISION 1.
    """
    newest_year = max(r["year"] for r in timeseries if r["researchers_per_1000"] != "")
    cutoff = newest_year - CURRENT_WINDOW_YEARS

    latest = {}
    for row in timeseries:  # rows are sorted by year, so later rows overwrite earlier
        entry = latest.setdefault(row["country_code"], {
            "country_code": row["country_code"],
            "country": row["country"],
            "is_aggregate": row["is_aggregate"],
            "researchers_year": "", "researchers_per_1000": "", "researchers_flags": "",
            "rd_personnel_year": "", "rd_personnel_per_1000": "", "rd_personnel_flags": "",
        })
        if row["researchers_per_1000"] != "":
            entry["researchers_year"] = row["year"]
            entry["researchers_per_1000"] = row["researchers_per_1000"]
            entry["researchers_flags"] = row["researchers_flags"]
        if row["rd_personnel_per_1000"] != "":
            entry["rd_personnel_year"] = row["year"]
            entry["rd_personnel_per_1000"] = row["rd_personnel_per_1000"]
            entry["rd_personnel_flags"] = row["rd_personnel_flags"]

    table = []
    for entry in latest.values():
        # Each ratio is judged on its own latest year.
        for prefix in ("researchers", "rd_personnel"):
            year = entry[f"{prefix}_year"]
            entry[f"{prefix}_is_current"] = year != "" and year > cutoff
        year = entry["researchers_year"]
        # Convert "per 1,000" to "percent of workforce" for plain-language text:
        # 17.8 per 1,000 = 1.78 per 100 = 1.78%.
        entry["researchers_pct_of_workforce"] = (
            round(entry["researchers_per_1000"] / 10, 3) if year != "" else ""
        )
        table.append(entry)
    # Highest first, which is the order the ranking chart uses.
    return sorted(table, key=lambda r: -(r["researchers_per_1000"] or 0))
# --- end snippet: latest ---


# --- snippet: sectors ---
SECTORS = {
    "B_RS": "Business",
    "G_RS": "Government",
    "H_RS": "Higher education",
}


def build_sector_shares(obs, labels, current_codes) -> list[dict[str, object]]:
    """Where each country's researchers work, in its latest year with all three sectors.

    The OECD publishes each sector's researchers as a percentage of the national
    total (unit PT_RSH). We use the most recent year in which ALL three sectors
    are reported, so the pieces come from the same year.

    The three sectors do not always add to 100%: the fourth Frascati sector,
    private non-profit institutions, is not in MSTI's researcher breakdown, and
    some countries report partial breakdowns. We show the remainder explicitly
    as "Other / unallocated" rather than rescaling the other three to fill it.
    """
    table = []
    for area in sorted(current_codes):
        years = [
            year for (a, measure, unit, year) in obs
            if a == area and measure == "B_RS" and unit == "PT_RSH"
        ]
        # Walk backwards from the newest year until all three sectors exist.
        for year in sorted(years, reverse=True):
            parts = {code: obs.get((area, code, "PT_RSH", year)) for code in SECTORS}
            if all(parts.values()):
                break
        else:
            continue  # this country never reports a complete breakdown

        remainder = 100 - sum(value(row) for row in parts.values())
        for code, sector in SECTORS.items():
            table.append({
                "country_code": area,
                "country": labels["CL_AREA"].get(area, area),
                "year": year,
                "sector": sector,
                "share_pct": round(value(parts[code]), 3),
                "flags": describe_flags(parts[code], labels),
            })
        table.append({
            "country_code": area,
            "country": labels["CL_AREA"].get(area, area),
            "year": year,
            "sector": "Other / unallocated",
            # Tiny negative remainders are rounding in the published shares.
            "share_pct": round(max(remainder, 0), 3),
            "flags": "Calculated by us as 100 minus the three published shares",
        })
    return table
# --- end snippet: sectors ---


def write_csv(name: str, rows: list[dict[str, object]]) -> None:
    """Write rows to data/processed/<name> with a header taken from the first row."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    # Write booleans as lowercase "true"/"false" so the article's JavaScript
    # (d3.autoType) reads them as real booleans rather than as text.
    rows = [
        {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in row.items()}
        for row in rows
    ]
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows):>5} rows to {path.relative_to(ARTICLE_DIR)}")


def main() -> None:
    labels = load_labels()
    obs = load_observations()

    checks = verify_ratios(obs)
    timeseries = build_timeseries(obs, labels)
    latest = build_latest(timeseries)
    current = {r["country_code"] for r in latest if r["researchers_is_current"]}
    sectors = build_sector_shares(obs, labels, current)

    write_csv("ratio_check.csv", checks)
    write_csv("researchers_timeseries.csv", timeseries)
    write_csv("latest_by_country.csv", latest)
    write_csv("sector_shares_latest.csv", sectors)


if __name__ == "__main__":
    main()

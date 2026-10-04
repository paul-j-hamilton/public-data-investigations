#!/usr/bin/env python3
"""Turn the preserved OECD snapshot into the analysis-ready tables used in the article.

This script never uses the network. It reads only the two files in data/raw/
(saved by fetch_data.py) and writes six CSV files to data/processed/:

- researchers_timeseries.csv  every country-year with researchers and R&D
                              personnel per 1,000 employed, plus status flags
- latest_by_country.csv       each country's most recent value (the rankings)
- sector_shares_latest.csv    where each country's researchers work: business,
                              government, or higher education
- aggregate_timeseries.csv    researchers as a share of the combined employment
                              of a fixed panel of countries (with China's
                              definition change adjusted), and its growth rate
- sector_vs_intensity.csv     business share of researchers vs. researchers per
                              1,000 employed, one row per country (same year)
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
import math
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

# DECISION 4: Which countries and years go into the all-country aggregate?
# Adding up researchers and employment across whichever countries happen to
# report in a given year would make the total jump whenever a country enters
# or leaves the data. So the aggregate uses a fixed (balanced) panel: only
# countries that report researchers in EVERY year from AGGREGATE_START to
# AGGREGATE_END. Nothing is interpolated. Countries that report only every
# other year (e.g. Sweden, Norway, Austria) drop out under this rule.
AGGREGATE_START = 1995
AGGREGATE_END = 2023

# DECISION 5: How to handle China's change of definition?
# China is about two-thirds of the panel's employment, so it dominates the
# pooled total. Before 2009, China counted researchers using the Frascati
# definition only in its independent research institutes; every other sector
# reported UNESCO "scientists and engineers", a broader group. From 2009 all
# sectors follow the Frascati Manual, and the OECD flags 2009 as a break in
# series. The 2008-to-2009 change in China's figure therefore mixes a change
# of definition with a year of real change.
#
# To put China's earlier years on the Frascati basis, we scale every pre-2009
# Chinese researcher count by one constant factor (a "level shift"). The size
# of the shift is the observed 2008-to-2009 log change MINUS the real growth
# China would plausibly have had that year. We take that real growth to be the
# average of China's log growth in the year before and the year after the
# break (2007-2008 and 2009-2010). Shifting by the whole observed change
# would instead assume China's research share did not grow at all in 2009.
BREAK_COUNTRY = "CHN"
BREAK_YEAR = 2009

# DECISION 6: How much to smooth the growth rate?
# Year-to-year growth is noisy (series breaks, recessions), so the growth chart
# also shows a Hodrick-Prescott trend. The HP filter needs a smoothing
# parameter; 6.25 is the standard choice for annual data (Ravn and Uhlig,
# 2002), equivalent to the familiar 1600 for quarterly data.
HP_SMOOTHING = 6.25
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
    # The one label we replace: the OECD's "Chinese Taipei" is shown as "Taiwan",
    # the name most English-language readers know.
    labels["CL_AREA"]["TWN"] = "Taiwan"
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

    researchers_per_1000_adjusted is the same as researchers_per_1000 except
    for China before BREAK_YEAR, where it applies the level shift from
    DECISION 5 (see china_level_shift). The trend chart uses this column; the
    rankings use the published figures, which are unaffected because every
    country's latest year is after the break.
    """
    shift = china_level_shift(obs)
    country_years = {
        (area, year)
        for (area, measure, unit, year) in obs
        if unit == "10P3EMP" and measure in ("T_RS", "T_TT")
    }
    table = []
    for area, year in sorted(country_years):
        researchers = obs.get((area, "T_RS", "10P3EMP", year))
        personnel = obs.get((area, "T_TT", "10P3EMP", year))
        shifted = bool(researchers) and area == BREAK_COUNTRY and year < BREAK_YEAR
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
            "researchers_per_1000_adjusted": (
                round(value(researchers) * (shift["factor"] if shifted else 1), 4)
                if researchers else ""
            ),
            "researchers_shifted": shifted,
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


# --- snippet: sector-fit ---
def build_sector_fit(sectors, timeseries) -> tuple[list[dict[str, object]], dict[str, float]]:
    """Does a larger research workforce go with a larger business share?

    One point per country: the share of its researchers who work in business
    (x) against its researchers per 1,000 employed (y). Both come from the
    SAME year, the latest year with a complete sector breakdown, so the two
    numbers describe the same moment. Countries with no researcher ratio in
    that year are left out, and so are the OECD and EU totals (DECISION 2).

    The line is an ordinary least-squares fit, y = intercept + slope x.
    r is the correlation and r_squared the share of the cross-country
    variation in y that the line accounts for.
    """
    ratio = {(r["country_code"], r["year"]): r["researchers_per_1000"] for r in timeseries}
    points = []
    for row in sectors:
        if row["sector"] != "Business" or row["country_code"] in AGGREGATES:
            continue
        y = ratio.get((row["country_code"], row["year"]), "")
        if y == "":
            continue
        points.append({
            "country_code": row["country_code"],
            "country": row["country"],
            "year": row["year"],
            "business_share_pct": row["share_pct"],
            "researchers_per_1000": y,
        })

    n = len(points)
    xs = [p["business_share_pct"] for p in points]
    ys = [p["researchers_per_1000"] for p in points]
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    syy = sum((y - mean_y) ** 2 for y in ys)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = sxy / sxx
    fit = {
        "n": n,
        "slope": slope,
        "intercept": mean_y - slope * mean_x,
        "r": sxy / math.sqrt(sxx * syy),
        "r_squared": sxy ** 2 / (sxx * syy),
    }
    return points, fit
# --- end snippet: sector-fit ---


# --- snippet: hp-filter ---
def hp_trend(series: list[float], smoothing: float) -> list[float]:
    """Hodrick-Prescott trend of a series.

    The trend t minimises
        sum (y - t)^2  +  smoothing x sum (second difference of t)^2,
    i.e. it stays close to the data but is penalised for changing direction.
    The minimum solves the linear system (I + smoothing x D'D) t = y, where D
    takes second differences. We build that small matrix and solve it by
    Gaussian elimination, so no numerical library is needed.
    """
    n = len(series)
    # D'D for second differences, written out directly.
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n - 2):
        weights = {i: 1.0, i + 1: -2.0, i + 2: 1.0}
        for r, wr in weights.items():
            for c, wc in weights.items():
                matrix[r][c] += smoothing * wr * wc
    for i in range(n):
        matrix[i][i] += 1.0

    # Gaussian elimination. The matrix is symmetric positive definite, so no
    # pivoting is needed.
    rhs = list(series)
    for col in range(n):
        for row in range(col + 1, n):
            factor = matrix[row][col] / matrix[col][col]
            if factor:
                for k in range(col, n):
                    matrix[row][k] -= factor * matrix[col][k]
                rhs[row] -= factor * rhs[col]
    trend = [0.0] * n
    for row in reversed(range(n)):
        known = sum(matrix[row][k] * trend[k] for k in range(row + 1, n))
        trend[row] = (rhs[row] - known) / matrix[row][row]
    return trend
# --- end snippet: hp-filter ---


# --- snippet: aggregate ---
def china_level_shift(obs) -> dict[str, float]:
    """Estimate the definitional step in China's series at BREAK_YEAR (DECISION 5).

    All changes are in natural logs of researchers per 1,000 employed:

        observed     = ln s(2009) - ln s(2008)          the jump in the data
        underlying   = average of ln s(2008) - ln s(2007)
                       and        ln s(2010) - ln s(2009)  assumed real change
        step         = observed - underlying             the change of definition
        factor       = exp(step)                          multiply pre-2009 counts by this
    """
    def log_ratio(year):
        return math.log(value(obs[(BREAK_COUNTRY, "T_RS", "10P3EMP", year)]))

    y = BREAK_YEAR
    observed = log_ratio(y) - log_ratio(y - 1)
    before = log_ratio(y - 1) - log_ratio(y - 2)
    after = log_ratio(y + 1) - log_ratio(y)
    underlying = (before + after) / 2
    step = observed - underlying
    return {
        "observed": observed, "before": before, "after": after,
        "underlying": underlying, "step": step, "factor": math.exp(step),
    }


def build_aggregate(obs, labels) -> tuple[list[dict[str, object]], list[str], dict[str, float]]:
    """The share of all employed people who are researchers, pooled across countries.

    For each year, add up full-time-equivalent researchers across a group of
    countries and divide by the group's total employment:

        share_t = sum of researchers (FTE) / sum of employment x 1,000

    This weights each country by its workforce, so it is the share of the
    *combined* labor force doing research, not an average of country shares.

    Four series are written:
      - the panel (DECISION 4) excluding China;
      - China alone, exactly as published (the break is left in);
      - China's pre-2009 years after the level shift (DECISION 5), to show
        the adjustment;
      - the whole panel including China, with China's pre-2009 researcher
        counts level-shifted. This is the headline series.

    For the two pooled series, the growth rate is the change in the natural
    log, d log s in the equation above, in percent: 100 x (ln share_t -
    ln share_t-1). The trend growth rate applies the HP filter (DECISION 6)
    to ln share and takes the year-to-year change in that trend.
    Each year also lists the countries the OECD flags with a break in series.
    """
    years = range(AGGREGATE_START, AGGREGATE_END + 1)
    panel = sorted(
        area for area in {a for (a, m, u, y) in obs if m == "T_RS" and u == "FTE"}
        if area not in AGGREGATES
        and all((area, "T_RS", "FTE", y) in obs and (area, "TOT_EMP", "PS", y) in obs for y in years)
    )
    shift = china_level_shift(obs)
    china = labels["CL_AREA"][BREAK_COUNTRY].split(" (")[0]   # "China"

    def researchers(area, year, adjust):
        count = value(obs[(area, "T_RS", "FTE", year)])
        if adjust and area == BREAK_COUNTRY and year < BREAK_YEAR:
            count *= shift["factor"]
        return count

    groups = [
        # (series name, member countries, apply China level shift?, years, compute growth?)
        (f"{len(panel) - 1} countries excluding {china}",
         [a for a in panel if a != BREAK_COUNTRY], False, years, True),
        (f"{china} (as published)", [BREAK_COUNTRY], False, years, False),
        (f"{china}, pre-{BREAK_YEAR} level-shifted", [BREAK_COUNTRY], True,
         range(AGGREGATE_START, BREAK_YEAR), False),
        (f"All {len(panel)} countries ({china} level-shifted)", panel, True, years, True),
    ]

    table = []
    for series, members, adjust, span, with_growth in groups:
        fte = [sum(researchers(a, y, adjust) for a in members) for y in span]
        employment = [sum(value(obs[(a, "TOT_EMP", "PS", y)]) for a in members) for y in span]
        log_share = [math.log(r / e * 1000) for r, e in zip(fte, employment)]
        log_trend = hp_trend(log_share, HP_SMOOTHING) if with_growth else None
        for i, year in enumerate(span):
            breaks = [
                labels["CL_AREA"].get(a, a) for a in members
                if has_break(obs[(a, "T_RS", "10P3EMP", year)])
            ]
            table.append({
                "series": series,
                "year": year,
                "countries": len(members),
                "researchers_fte": round(fte[i]),
                "employment": round(employment[i]),
                "researchers_per_1000": round(math.exp(log_share[i]), 4),
                # True where China's count has been multiplied by the shift factor.
                "china_shifted": adjust and BREAK_COUNTRY in members and year < BREAK_YEAR,
                # Which side of China's definition change this year is on;
                # the chart uses it to leave a gap in China's published line.
                "china_definition": "Frascati" if year >= BREAK_YEAR else "pre-Frascati",
                "growth_pct": round(100 * (log_share[i] - log_share[i - 1]), 3)
                              if with_growth and i else "",
                "trend_growth_pct": round(100 * (log_trend[i] - log_trend[i - 1]), 3)
                                    if with_growth and i else "",
                "breaks": "; ".join(breaks),
            })
    return table, [labels["CL_AREA"].get(a, a) for a in panel], shift
# --- end snippet: aggregate ---


# --- snippet: facts ---
def write_facts(obs, checks, aggregate, panel_names, shift, sector_fit) -> None:
    """Save the counts quoted in the article's text to data/processed/facts.yml.

    The article reads this file (via `metadata-files` in its front matter) and
    prints the values with {{< meta facts.NAME >}}, so numbers in the prose are
    computed here rather than typed by hand, and update when the data does.
    """
    with RAW_DATA.open(encoding="utf-8-sig", newline="") as source:
        raw_rows = sum(1 for _ in csv.DictReader(source))
    facts = {
        "raw_rows": f"{raw_rows:,}",          # e.g. "27,330"
        "ratios_checked": f"{len(checks):,}",  # e.g. "2,785"
        "aggregate_start": str(AGGREGATE_START),
        "aggregate_end": str(AGGREGATE_END),
        "aggregate_countries": str(len(panel_names)),
        "aggregate_country_list": ", ".join(panel_names),
    }
    names_all = list(dict.fromkeys(r["series"] for r in aggregate))[-1]   # all panel countries

    # China's level shift (DECISION 5), as percentages and as the multiplier.
    facts["china_break_year"] = str(BREAK_YEAR)
    for key in ("observed", "before", "after", "underlying", "step"):
        facts[f"china_{key}_pct"] = f"{100 * shift[key]:.1f}"
    facts["china_factor"] = f"{shift['factor']:.3f}"
    facts["china_factor_pct"] = f"{100 * (1 - shift['factor']):.0f}"   # "reduced by N%"
    facts["aggregate_countries_ex_china"] = str(len(panel_names) - 1)

    # Coverage: how many people the data and the panel cover in AGGREGATE_END.
    # Countries with at least one "per 1,000 employed" figure. (Argentina
    # reports researcher counts but no employment, so it has no share.)
    countries = {a for (a, m, u, y) in obs if u == "10P3EMP" and a not in AGGREGATES}
    msti_employment = sum(
        value(obs[(a, "TOT_EMP", "PS", AGGREGATE_END)])
        for a in countries if (a, "TOT_EMP", "PS", AGGREGATE_END) in obs
    )
    panel_end = [r for r in aggregate if r["series"] == names_all and r["year"] == AGGREGATE_END][0]
    china_employment = value(obs[(BREAK_COUNTRY, "TOT_EMP", "PS", AGGREGATE_END)])
    facts["msti_countries"] = str(len(countries))
    facts["msti_employment_bn"] = f"{msti_employment / 1e9:.1f}"
    facts["aggregate_employment_bn"] = f"{panel_end['employment'] / 1e9:.2f}"
    facts["aggregate_researchers_m"] = f"{panel_end['researchers_fte'] / 1e6:.1f}"
    facts["china_employment_m"] = f"{china_employment / 1e6:.0f}"
    facts["china_employment_share_pct"] = f"{100 * china_employment / panel_end['employment']:.0f}"

    # Start and end values, and the average annual growth rate, of the two
    # pooled series: excluding China (first) and all countries (last).
    names = list(dict.fromkeys(r["series"] for r in aggregate))
    for key, series in (("ex_china", names[0]), ("all", names[-1])):
        rows = [r for r in aggregate if r["series"] == series]
        first, last = rows[0], rows[-1]
        growth = 100 * math.log(last["researchers_per_1000"] / first["researchers_per_1000"]) / (
            last["year"] - first["year"])
        facts[f"aggregate_{key}_first"] = f"{first['researchers_per_1000']:.1f}"
        facts[f"aggregate_{key}_last"] = f"{last['researchers_per_1000']:.1f}"
        facts[f"aggregate_{key}_growth"] = f"{growth:.1f}"
    # Business share vs. researcher intensity fit.
    facts["sector_fit_n"] = str(sector_fit["n"])
    facts["sector_fit_slope_10pp"] = f"{10 * sector_fit['slope']:.1f}"   # per 10 points of business share
    facts["sector_fit_r"] = f"{sector_fit['r']:.2f}"
    facts["sector_fit_r2"] = f"{sector_fit['r_squared']:.2f}"
    facts["sector_fit_r2_pct"] = f"{100 * sector_fit['r_squared']:.0f}"

    lines = ["# Generated by scripts/analyze.py. Do not edit by hand.", "facts:"]
    lines += [f'  {key}: "{value}"' for key, value in facts.items()]
    path = OUT_DIR / "facts.yml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(facts):>5} facts to {path.relative_to(ARTICLE_DIR)}")
# --- end snippet: facts ---


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
    aggregate, panel_names, shift = build_aggregate(obs, labels)
    sector_points, sector_fit = build_sector_fit(sectors, timeseries)

    write_csv("ratio_check.csv", checks)
    write_csv("researchers_timeseries.csv", timeseries)
    write_csv("latest_by_country.csv", latest)
    write_csv("sector_shares_latest.csv", sectors)
    write_csv("aggregate_timeseries.csv", aggregate)
    write_csv("sector_vs_intensity.csv", sector_points)
    write_facts(obs, checks, aggregate, panel_names, shift, sector_fit)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Draw the small preview image shown on the site's home page.

It is built from data/processed/latest_by_country.csv (the same table as the
article's ranking chart), so the thumbnail can never show different numbers
from the article. Pure standard library: it writes SVG text directly.
"""

from __future__ import annotations

import csv
from pathlib import Path

ARTICLE_DIR = Path(__file__).resolve().parents[1]
SOURCE = ARTICLE_DIR / "data" / "processed" / "latest_by_country.csv"
OUTPUT = ARTICLE_DIR / "rd-workforce.svg"

TOP_N = 12                     # how many bars to draw
WIDTH, HEIGHT = 800, 440       # overall image size in pixels
LABEL_W, TOP, BAR_H, GAP = 190, 24, 26, 7


def main() -> None:
    with SOURCE.open(encoding="utf-8", newline="") as source:
        rows = [
            r for r in csv.DictReader(source)
            # Same rule as the article: current countries and aggregates only.
            if r["researchers_is_current"] == "true" and r["researchers_per_1000"]
        ]
    rows.sort(key=lambda r: -float(r["researchers_per_1000"]))
    rows = rows[:TOP_N]
    scale = (WIDTH - LABEL_W - 70) / float(rows[0]["researchers_per_1000"])

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
        'font-family="system-ui, sans-serif" font-size="15">',
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="#f7f5f0"/>',
    ]
    for i, row in enumerate(rows):
        y = TOP + i * (BAR_H + GAP)
        value = float(row["researchers_per_1000"])
        fill = "#8c8c8c" if row["is_aggregate"] == "true" else "#136f63"
        name = row["country"].replace("&", "&amp;")
        parts += [
            f'<text x="{LABEL_W - 10}" y="{y + BAR_H * 0.7:.1f}" text-anchor="end" fill="#18302d">{name}</text>',
            f'<rect x="{LABEL_W}" y="{y}" width="{value * scale:.1f}" height="{BAR_H}" rx="3" fill="{fill}"/>',
            f'<text x="{LABEL_W + value * scale + 8:.1f}" y="{y + BAR_H * 0.7:.1f}" fill="#52615e">{value:.1f}</text>',
        ]
    parts.append("</svg>")
    OUTPUT.write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ARTICLE_DIR)}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Match rail-rover station names (data/coords.json) to CIF CRS codes, using
the CIF Master Station Names file (data/cif/RJTTF*.MSN, "A" records: name at
columns 6-30, CRS at columns 44-46).

Output: data/station_crs.json -- {station name: CRS code}

Unmatched/ambiguous stations are printed as warnings; add corrections to
station_overrides.json (name -> CRS) following rail-rover's overrides.json
pattern, then re-run.
"""

import difflib
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
CIF_DIR = ROOT / "data" / "cif"
COORDS_FILE = ROOT / "data" / "coords.json"
OVERRIDES_FILE = ROOT / "station_overrides.json"
OUTPUT = ROOT / "data" / "station_crs.json"

MATCH_THRESHOLD = 0.7


def normalise(name):
    name = name.upper()
    name = re.sub(r"\(.*?\)", "", name)  # drop "(Manchester)"-style suffixes
    name = name.replace("&", " AND ")
    name = re.sub(r"[^A-Z0-9 ]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def find_msn():
    candidates = sorted(CIF_DIR.glob("*.MSN")) + sorted(CIF_DIR.glob("*.msn"))
    if not candidates:
        raise SystemExit(f"No .MSN file found in {CIF_DIR} -- run download_timetable.py first")
    return candidates[0]


def main():
    msn_path = find_msn()

    # name (raw) -> crs, plus a normalised-name -> [(raw name, crs)] index for matching
    msn_entries = []
    with open(msn_path, encoding="latin-1") as f:
        for line in f:
            if line.startswith("A    ") and len(line) >= 46:
                name = line[5:30].strip()
                crs = line[43:46].strip()
                if name and crs:
                    msn_entries.append((name, crs))

    norm_to_crs = {}
    for name, crs in msn_entries:
        norm_to_crs.setdefault(normalise(name), crs)
    norm_names = list(norm_to_crs)

    coords = json.load(open(COORDS_FILE))
    overrides = {}
    if OVERRIDES_FILE.exists():
        overrides = json.load(open(OVERRIDES_FILE)).get("station_crs", {})

    result = {}
    unmatched = []
    fuzzy = []

    for station in sorted(coords):
        if station in overrides:
            result[station] = overrides[station]
            continue

        norm = normalise(station)
        if norm in norm_to_crs:
            result[station] = norm_to_crs[norm]
            continue

        match = difflib.get_close_matches(norm, norm_names, n=1, cutoff=MATCH_THRESHOLD)
        if match:
            result[station] = norm_to_crs[match[0]]
            fuzzy.append((station, match[0], norm_to_crs[match[0]]))
        else:
            unmatched.append(station)

    OUTPUT.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"Matched {len(result)}/{len(coords)} stations to CRS codes -> {OUTPUT}")

    if fuzzy:
        print(f"\n{len(fuzzy)} fuzzy match(es) (review these):")
        for station, msn_name, crs in fuzzy:
            print(f"  {station!r} -> {msn_name!r} ({crs})")

    if unmatched:
        print(f"\n{len(unmatched)} UNMATCHED station(s) (add to station_overrides.json's "
              f"\"station_crs\" section):")
        for station in unmatched:
            print(f"  {station!r}")


if __name__ == "__main__":
    main()

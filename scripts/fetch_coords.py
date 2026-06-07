#!/usr/bin/env python3
"""
Build a station name -> [lat, lon] coordinate dictionary for all stations
that appear in data/tickets_raw.json.

Strategy:
1. Bulk-fetch all UK railway stations from the OSM Overpass API.
2. Direct name match, then fuzzy match (strip parentheticals, case-insensitive).
3. A small hardcoded table covers stations with ampersands or unusual names
   that OSM lists differently.

Outputs: data/coords.json
"""

import json, re, urllib.request, urllib.parse
from pathlib import Path

TICKETS_FILE = Path(__file__).parent.parent / "data" / "tickets_raw.json"
OUTPUT       = Path(__file__).parent.parent / "data" / "coords.json"

OVERPASS_QUERY = """
[out:json][timeout:90];
(
  node["railway"="station"](49,-8,61,2);
  node["railway"="halt"](49,-8,61,2);
);
out body;
"""

# Stations that OSM names differently from National Rail
MANUAL = {
    "Abergele & Pensarn":         [53.287, -3.585],
    "Ansdell & Fairhaven":        [53.741, -2.970],
    "Arrochar & Tarbet":          [56.197, -4.726],
    "Barnsley":                   [53.554, -1.477],
    "Birkenhead Hamilton Square": [53.393, -3.018],
    "Cark & Cartmel":             [54.188, -2.908],
    "Church & Oswaldtwistle":     [53.751, -2.375],
    "Dore & Totley":              [53.326, -1.536],
    "Dunkeld & Birnam":           [56.558, -3.578],
    "Edinburgh":                  [55.952, -3.190],
    "Elton & Orston":             [52.937, -0.906],
    "Gillingham Kent":            [51.386,  0.549],
    "Hall-i'-th'-Wood":           [53.597, -2.424],
    "Hayes & Harlington":         [51.506, -0.422],
    "Hope Derbyshire":            [53.346, -1.722],
    "Hope Flintshire":            [53.100, -3.047],
    "Hoveton & Wroxham":          [52.713,  1.406],
    "Hull":                       [53.744, -0.347],
    "IBM":                        [55.906, -4.393],
    "Ince & Elton (Cheshire)":    [53.280, -2.830],
    "Kings Sutton":               [52.040, -1.305],
    "Kirkham & Wesham":           [53.787, -2.877],
    "Lazonby & Kirkoswald":       [54.725, -2.714],
    "Lisvane & Thornhill":        [51.544, -3.165],
    "Meadowhall":                 [53.413, -1.421],
    "North Llanrwst":             [53.125, -3.798],
    "Partick":                    [55.872, -4.319],
    "Pembrey & Burry Port":       [51.692, -4.240],
    "Pontypool & New Inn":        [51.702, -3.014],
    "Possilpark & Parkhouse":     [55.888, -4.259],
    "Ramsgreave & Wilpshire":     [53.789, -2.447],
    "Ravenglass for Eskdale":     [54.355, -3.407],
    "Risca & Pontymister":        [51.607, -3.089],
    "Sandal & Agbrigg":           [53.659, -1.494],
    "Shepherds Well":             [51.199,  1.268],
    "Stanlow & Thornton":         [53.267, -2.861],
    "Steeton & Silsden":          [53.897, -1.933],
    "Swinton (South Yorks)":      [53.509, -1.312],
    "Trefforest Estate":          [51.581, -3.286],
    "Ty Glas":                    [51.534, -3.196],
    "Windsor & Eton Central":     [51.480, -0.618],
}


def fetch_osm():
    print("Fetching UK station coordinates from OSM Overpass API...")
    data = urllib.parse.urlencode({"data": OVERPASS_QUERY}).encode()
    req  = urllib.request.Request(
        "https://overpass-api.de/api/interpreter", data=data,
        headers={"User-Agent": "RailRoverApp/1.0"}
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode())

    osm = {}
    for el in result.get("elements", []):
        name = el.get("tags", {}).get("name", "").strip()
        lat, lon = el.get("lat"), el.get("lon")
        if name and lat and lon:
            if name not in osm or el.get("tags", {}).get("network") == "National Rail":
                osm[name] = [round(lat, 4), round(lon, 4)]
    print(f"  Got {len(osm)} named stations from OSM")
    return osm


def match_stations(needed, osm):
    coords = {}
    unmatched = []

    for s in needed:
        if s in osm:
            coords[s] = osm[s]
            continue

        # Strip parenthetical suffix e.g. "(Cheshire)"
        stripped = re.sub(r"\s*\([^)]+\)\s*$", "", s).strip()
        osm_lower = {k.lower(): v for k, v in osm.items()}

        if stripped in osm:
            coords[s] = osm[stripped]
        elif s + " Station" in osm:
            coords[s] = osm[s + " Station"]
        elif s.lower() in osm_lower:
            coords[s] = osm_lower[s.lower()]
        elif stripped.lower() in osm_lower:
            coords[s] = osm_lower[stripped.lower()]
        else:
            unmatched.append(s)

    return coords, unmatched


def main():
    with open(TICKETS_FILE) as f:
        tickets = json.load(f)

    needed = sorted({s for t in tickets for s in t["stations"]})
    print(f"Need coordinates for {len(needed)} unique stations")

    osm = fetch_osm()
    coords, unmatched = match_stations(needed, osm)

    # Apply manual table
    for name, coord in MANUAL.items():
        if name in needed:
            coords[name] = coord
            if name in unmatched:
                unmatched.remove(name)

    if unmatched:
        print(f"\nWARNING: {len(unmatched)} stations still unmatched:")
        for s in unmatched:
            print(f"  {s}")

    OUTPUT.parent.mkdir(exist_ok=True)
    with open(OUTPUT, "w") as f:
        json.dump(coords, f, indent=2, ensure_ascii=False)
    print(f"\nSaved {len(coords)} coordinates to {OUTPUT}")


if __name__ == "__main__":
    main()

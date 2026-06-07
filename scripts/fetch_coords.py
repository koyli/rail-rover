#!/usr/bin/env python3
"""
Build a station name -> [lat, lon] coordinate dictionary for all stations
that appear in data/tickets_raw.json.

Strategy:
  National Rail's own station directory is the authoritative source for both
  station names AND coordinates -- and unlike OpenStreetMap, NR's data is
  already disambiguated by region (e.g. "Whitchurch (Cardiff)" vs
  "Whitchurch (Shropshire)" are distinct entries with their own coordinates).
  An earlier OSM-based approach stripped these "(Region)" qualifiers to fuzzy
  -match station names, which silently collapsed multiple distinct stations
  onto a single OSM entry whenever OSM only tagged one regional variant with
  the bare name (e.g. both Whitchurch stations matched OSM's solitary
  "Whitchurch", which is actually in Hampshire).

  For each station name we need:
   1. Query NR's public station-search index (Algolia; the credentials are
      a search-only key embedded in nationalrail.co.uk's page data) for an
      exact name match, which yields a URL slug like "whitchurch-shropshire".
   2. Fetch that station's detail page at nationalrail.co.uk/stations/{slug}/
      and read the precise lat/lon out of its embedded __NEXT_DATA__ JSON.

Outputs: data/coords.json
"""

import json, re, subprocess, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

TICKETS_FILE = Path(__file__).parent.parent / "data" / "tickets_raw.json"
OUTPUT       = Path(__file__).parent.parent / "data" / "coords.json"

ALGOLIA_URL = "https://uaidcukgz5-dsn.algolia.net/1/indexes/site-search-prod/query"
ALGOLIA_KEY = "4a8ad6ab414cd909a260fc6d15d004f7"
ALGOLIA_APP = "UAIDCUKGZ5"
STATION_URL = "https://www.nationalrail.co.uk/stations/{slug}/"

WORKERS = 5


def curl(args, data=None):
    cmd = ["curl", "-s", "--max-time", "20"] + args
    r = subprocess.run(cmd, input=data, capture_output=True, text=True)
    return r.stdout


RETRIES = 4


def algolia_search(name):
    body = json.dumps({"query": name, "hitsPerPage": 5, "filters": "type:Station"})
    for attempt in range(RETRIES):
        out = curl([
            "-X", "POST", ALGOLIA_URL,
            "-H", f"X-Algolia-API-Key: {ALGOLIA_KEY}",
            "-H", f"X-Algolia-Application-Id: {ALGOLIA_APP}",
            "-H", "Content-Type: application/json",
            "-H", "Referer: https://www.nationalrail.co.uk/",
            "-d", body,
        ])
        try:
            hits = json.loads(out).get("hits", [])
            if hits:
                return hits
        except Exception:
            pass
        time.sleep(0.5 * (attempt + 1))
    return []


def station_location(slug):
    for attempt in range(RETRIES):
        html = curl([STATION_URL.format(slug=slug)])
        m = re.search(r'"location":\{"lat":(-?[\d.]+),"lon":(-?[\d.]+)\}', html)
        if m:
            return [round(float(m.group(1)), 4), round(float(m.group(2)), 4)]
        time.sleep(0.5 * (attempt + 1))
    return None


def lookup(name):
    hits = algolia_search(name)
    match = next((h for h in hits if h.get("name", "").strip().lower() == name.strip().lower()), None)
    if not match:
        return name, None, f"no exact match (candidates: {[h.get('name') for h in hits[:3]]})"
    slug = match["url"].strip("/").split("/")[-1]
    coord = station_location(slug)
    if not coord:
        return name, None, f"matched slug={slug} but no location data on its page"
    return name, coord, None


def main():
    with open(TICKETS_FILE) as f:
        tickets = json.load(f)

    needed = sorted({s for t in tickets for s in t["stations"]})
    print(f"Need coordinates for {len(needed)} unique stations")

    # Resume from a previous partial run if coords.json already has entries
    # (NR's search API rate-limits under concurrency, so a first pass may
    # leave some stations unmatched -- re-running only retries those).
    coords = {}
    if OUTPUT.exists():
        with open(OUTPUT) as f:
            coords = {k: v for k, v in json.load(f).items() if k in needed}
    todo = [n for n in needed if n not in coords]
    print(f"  {len(coords)} already cached, looking up {len(todo)} remaining "
          f"against National Rail's authoritative station directory...")

    unmatched = []
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(lookup, name): name for name in todo}
        for fut in as_completed(futures):
            name, coord, err = fut.result()
            done += 1
            if coord:
                coords[name] = coord
            else:
                unmatched.append((name, err))
            if done % 100 == 0:
                elapsed = time.time() - t0
                print(f"  {done}/{len(todo)} ({elapsed:.0f}s elapsed)", flush=True)

    print(f"\nMatched {len(coords)}/{len(needed)} total ({time.time()-t0:.0f}s for this run)")

    if unmatched:
        print(f"\nWARNING: {len(unmatched)} stations could not be matched:")
        for name, err in unmatched:
            print(f"  {name}: {err}")

    OUTPUT.parent.mkdir(exist_ok=True)
    with open(OUTPUT, "w") as f:
        json.dump(dict(sorted(coords.items())), f, indent=2, ensure_ascii=False)
    print(f"\nSaved {len(coords)} coordinates to {OUTPUT}")


if __name__ == "__main__":
    main()

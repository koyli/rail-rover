#!/usr/bin/env python3
"""
Build a station name -> [lat, lon] coordinate dictionary covering every
station in National Rail's official directory.

Strategy:
  NR publishes a sitemap listing every station's detail page
  (nationalrail.co.uk/sitemaps/sitemap-stations.xml has exactly 2,612 <loc>
  entries, matching the directory's own station count). Each detail page
  embeds the station's canonical name and precise lat/lon in its
  __NEXT_DATA__ JSON -- and unlike OpenStreetMap, NR's data is already
  disambiguated by region (e.g. "Whitchurch (Cardiff)" vs "Whitchurch
  (Shropshire)" are distinct entries with their own coordinates and names).

  This sitemap-driven approach also naturally yields the *complete* station
  list (not just the ~1,850 referenced by name in scraped ticket data),
  which build_html.py uses to populate "All Line Rover" style tickets that
  cover the whole network.

Outputs: data/coords.json
"""

import json, re, subprocess, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

SITEMAP_URL = "https://www.nationalrail.co.uk/sitemaps/sitemap-stations.xml"
STATION_URL = "https://www.nationalrail.co.uk/stations/{slug}/"
OUTPUT      = Path(__file__).parent.parent / "data" / "coords.json"

WORKERS = 5
RETRIES = 4


def curl(url):
    cmd = ["curl", "-s", "--max-time", "20", "-H", "User-Agent: Mozilla/5.0", url]
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def station_slugs():
    xml = curl(SITEMAP_URL)
    locs = re.findall(r"<loc>([^<]+)</loc>", xml)
    return sorted({loc.rstrip("/").split("/")[-1] for loc in locs})


def station_detail(slug):
    for attempt in range(RETRIES):
        html = curl(STATION_URL.format(slug=slug))
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if m:
            try:
                station = json.loads(m.group(1))["props"]["pageProps"]["station"]
                loc = station["location"]
                # NR's own data has stray trailing whitespace on a few names
                # (e.g. "Motherwell ", "Scunthorpe ") -- normalise it away
                name = re.sub(r"\s+", " ", station["name"]).strip()
                return slug, name, [round(loc["lat"], 4), round(loc["lon"], 4)]
            except Exception:
                pass
        time.sleep(0.5 * (attempt + 1))
    return slug, None, None


def fetch_all(slugs):
    coords = {}
    failed = []
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(station_detail, slug): slug for slug in slugs}
        for fut in as_completed(futures):
            slug, name, coord = fut.result()
            done += 1
            if name and coord:
                coords[name] = coord
            else:
                failed.append(slug)
            if done % 200 == 0:
                print(f"  {done}/{len(slugs)} ({time.time() - t0:.0f}s elapsed)", flush=True)
    return coords, failed


def main():
    slugs = station_slugs()
    print(f"National Rail directory lists {len(slugs)} stations")

    coords, failed = fetch_all(slugs)

    if failed:
        print(f"\n{len(failed)} station pages didn't respond in time -- retrying serially...")
        still_failed = []
        for slug in failed:
            time.sleep(1)
            _, name, coord = station_detail(slug)
            if name and coord:
                coords[name] = coord
            else:
                still_failed.append(slug)
        failed = still_failed

    print(f"\nFetched {len(coords)}/{len(slugs)} stations")
    if failed:
        print(f"\nWARNING: {len(failed)} station pages could not be read:")
        for slug in failed:
            print(f"  {slug}")

    OUTPUT.parent.mkdir(exist_ok=True)
    with open(OUTPUT, "w") as f:
        json.dump(dict(sorted(coords.items())), f, indent=2, ensure_ascii=False)
    print(f"\nSaved {len(coords)} coordinates to {OUTPUT}")


if __name__ == "__main__":
    main()

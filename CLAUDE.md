# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-page interactive Leaflet.js map of all UK rail rover/ranger tickets
(`index.html`), generated from scraped National Rail data plus manual
corrections. Live at https://koyli.github.io/rail-rover/.

## Pipeline / commands

```bash
# 1. Scrape all ranger/rover tickets from National Rail (~95 tickets, ~30s)
python3 scripts/fetch_tickets.py        # -> data/tickets_raw.json

# 2. Fetch coordinates for every station in the NR network (~2,611 stations, ~8min)
python3 scripts/fetch_coords.py         # -> data/coords.json

# 3. (Optional) Extract current rover/ranger pricing from the RSP Fares Data Feed
NRDP_USERNAME=... OUTPUT_FILE=data/fares.zip ./scripts/download_fares.sh
unzip -o data/fares.zip -d data/fares
python3 scripts/fetch_rover_prices.py   # -> data/rover_prices.json

# 4. Regenerate index.html from the data files + overrides.json
python3 scripts/build_html.py           # -> index.html
```

There is no build/test framework — `index.html` is the deployable artifact,
self-contained (Leaflet loaded from a CDN with SRI hashes). After editing
`overrides.json` or any script, re-run `build_html.py` and check its printed
warnings (missing coordinates, override results, etc.) before considering the
change done.

`download_fares.sh` requires NRDP credentials and downloads a ~300MB feed
(`data/fares/`, gitignored) — not run as part of a normal rebuild.
`fetch_rover_prices.py` matches the feed's Rail Rovers file against ticket
names; only matches scoring ≥0.85 are written to `data/rover_prices.json`
(committed, small). See README.md for how this interacts with
`overrides.json`'s `pricing` block.

## Architecture

- `data/tickets_raw.json` — raw scrape output: one entry per ticket with
  `id`, `name`, `url`, `description`, `operator`, `stations[]`,
  `stations_complete`, `applies_to_all_stations`, `validity_map_url`,
  `pricing[]`. This is the *only* file `fetch_tickets.py` writes; never hand-edit.
- `data/coords.json` — station name -> `[lat, lon]`, sourced from NR's own
  station detail pages (already region-disambiguated, e.g. "Whitchurch
  (Cardiff)" vs "Whitchurch (Shropshire)"). Never hand-edit — overwritten by
  `fetch_coords.py`. Coordinate fixes go in `overrides.json`'s
  `station_coords` instead.
- `overrides.json` — the only file meant for manual editing. Five sections,
  all applied by `build_html.py` in this order: `add_tickets` (whole tickets
  missing from NR's ranger-rover listing, e.g. tickets that live under NR's
  `ticket-types/tickets/` section instead), `remove_stations`,
  `add_stations`, `station_coords`, `pricing`. See README.md for the full
  schema and worked examples — it documents every correction currently
  applied and why, which is essential context before adding a new one.
- `data/rover_prices.json` — ticket id -> `pricing[]`, output of
  `fetch_rover_prices.py`. Applied by `build_html.py` as each matched
  ticket's pricing (overriding the NR scrape), before `overrides.json`'s
  `pricing` block (which still wins if both target the same ticket).
- `index.html` — generated output. Don't hand-edit; change `build_html.py`
  (for structure/behaviour) or `overrides.json` (for data corrections) and
  regenerate.

## Key conventions

- Station names must match `coords.json` exactly to get a map marker
  (`build_html.py` warns about any used station with no matching coordinate).
  Regional disambiguation suffixes like "(Manchester)" or "(Shropshire)"
  follow NR's own naming.
- `build_html.py` populates `applies_to_all_stations` tickets (e.g. All Line
  Rover) with every station in `coords.json`, *before* applying
  `remove_stations`/`add_stations` — so overrides can still tweak network-wide
  tickets.
- Ticket colours are generated deterministically by `gen_colors()` based on
  sort order (tickets are sorted alphabetically by name) — don't expect color
  assignments to be stable if the ticket set changes.
- When fetching pages from operator/NR websites for research (overrides,
  station lists, etc.), use a browser User-Agent string with `curl` — bare
  `curl` requests are often blocked.

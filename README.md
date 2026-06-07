# Rail Rover & Ranger Map

Interactive map of all UK rail rover and ranger tickets, sourced from [National Rail](https://www.nationalrail.co.uk/ticket-types/promotions/?promotionType=ranger-rover).

**Live:** https://koyli.github.io/rail-rover/

## What it does

- Shows all ~95 rover/ranger tickets on a Leaflet.js map
- Toggle tickets in the sidebar to highlight their coverage area
- Hover any station to see which tickets cover it and their adult price
- Hover a ticket in the sidebar for the full price breakdown (adult, child, concession, railcard discounts)
- Click the ↗ icon to open the ticket's page on the National Rail website
- Overlapping coverage areas stack with transparency so you can see where tickets share stations

## Repo structure

```
index.html          # The app — single self-contained file, ready to open
overrides.json      # Manual corrections to errors in National Rail's data
data/
  tickets_raw.json  # Scraped ticket data (stations, pricing) — output of fetch_tickets.py
  coords.json       # Station name -> [lat, lon] — output of fetch_coords.py
scripts/
  fetch_tickets.py  # Scrapes NR website for all tickets (reads __NEXT_DATA__ JSON)
  fetch_coords.py   # Fetches station coordinates from OSM Overpass API
  build_html.py     # Generates index.html from the data files
```

## Rebuilding from scratch

```bash
# 1. Scrape all tickets from National Rail (~95 tickets, takes ~30s)
python3 scripts/fetch_tickets.py

# 2. Fetch station coordinates for the entire National Rail network (~2,610 stations, takes ~8min)
python3 scripts/fetch_coords.py

# 3. Generate index.html
python3 scripts/build_html.py
```

The scripts write to `data/` and read `overrides.json` automatically.

## Correcting NR data errors

Add entries to `overrides.json` and re-run `build_html.py`:

```json
{
  "remove_stations": {
    "ticket-slug": ["Incorrectly Listed Station"]
  }
}
```

Known corrections:
- `thames-rover-7-day`: Denby Dale (a West Yorkshire station incorrectly listed)
- `freedom-of-severn-solent-8-in-15-day-rover`, `freedom-of-severn-solent-3-in-7-day-rover`: Penmere (incorrectly listed)

## Data sources

- Ticket data: [National Rail promotions](https://www.nationalrail.co.uk/ticket-types/promotions/) via embedded `__NEXT_DATA__` JSON
- Station coordinates: [National Rail's station directory](https://www.nationalrail.co.uk/stations/) — `fetch_coords.py` reads NR's `sitemap-stations.xml` to enumerate every station page (2,611 of the 2,612 listed; one brand-new station has no published location yet), then reads each one's exact lat/lon from its detail page (`/stations/{slug}/`). This is NR's own authoritative data, already disambiguated by region (e.g. "Whitchurch (Cardiff)" vs "Whitchurch (Shropshire)" each get their own correct coordinates rather than colliding on a shared base name, which an earlier OpenStreetMap-based approach was prone to). Because this covers the *entire* network rather than just stations referenced by name in ticket data, `build_html.py` also uses it to populate the station list for tickets NR marks as covering all stations (e.g. All Line Rover) explicitly, rather than leaving their coverage area blank

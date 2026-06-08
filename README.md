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
  "add_tickets": [
    {
      "id": "ticket-slug",
      "name": "Ticket Name",
      "url": "https://www.nationalrail.co.uk/...",
      "description": "Plain text description.",
      "operator": "Operator One, Operator Two",
      "stations": ["Station One", "Station Two"]
    }
  ],
  "remove_stations": {
    "ticket-slug": ["Incorrectly Listed Station"]
  },
  "add_stations": {
    "ticket-slug": ["Missing Station One", "Missing Station Two"]
  },
  "station_coords": {
    "Station With Wrong Coordinates": [51.1234, -0.5678]
  },
  "pricing": {
    "ticket-slug": [
      {"label": "4 in 8 days", "adultPrice": 155}
    ]
  }
}
```

- `add_tickets` injects a whole ticket that's missing from NR's "ranger-rover" promotions listing entirely (the thing `fetch_tickets.py` scrapes) -- e.g. tickets that live under NR's separate `ticket-types/tickets/` section instead, which has no station list or pricing embedded in the page. `stations`/`stations_complete`/`applies_to_all_stations`/`pricing` are optional and default to `[]`/`true`/`false`/`[]`; the other fields are required. Added tickets go through the same pipeline as scraped ones, so `remove_stations`/`add_stations`/`pricing` overrides can also target them by `id`
- `remove_stations` drops a station from a specific ticket's coverage area
- `add_stations` adds stations to a ticket's coverage area (for tickets whose NR page lists no stations at all, or is missing some) -- station names must match `coords.json` exactly (e.g. "Whitchurch (Shropshire)", not "Whitchurch"), or they won't get a map marker; `build_html.py` warns if an added name has no matching coordinates
- `station_coords` overrides a station's lat/lon everywhere on the map (for cases where NR's own station page has an error -- a direct edit to `coords.json` would just be overwritten the next time `fetch_coords.py` runs)
- `pricing` replaces a ticket's whole price list (for tickets whose NR page renders prices through an interactive widget rather than embedding them in the page data, so `fetch_tickets.py` can't scrape them) -- entries use the same shape as scraped pricing (`label`, `adultPrice`, `childPrice`, `concessionPrice`, `railcardPrices`, ...); fields you don't supply just won't be shown

Known corrections:
- `c-2-c-senior-rover`: NR's page lists no stations at all (`stations: []`, despite `stations_complete: true`); populated with all 28 stations on c2c's network from [c2c's own routes & stations page](https://www.c2c-online.co.uk/stations-and-services/before-your-journey/our-routes-and-stations/) (its station-finder dropdown), including London Liverpool Street and Stratford (London) -- c2c's alternative/weekend termini, confirmed as genuinely served via their own station pages, not just nearby interchanges; names matched to `coords.json`'s conventions (e.g. "Chafford Hundred Lakeside", "London Liverpool Street", "Stratford (London)")
- `thames-rover-7-day`: Denby Dale (a West Yorkshire station incorrectly listed)
- `freedom-of-severn-solent-8-in-15-day-rover`, `freedom-of-severn-solent-3-in-7-day-rover`: Penmere (incorrectly listed)
- Burnham-on-Crouch: NR's own station page places it in the North Sea (`51.8378, 2.3082`, ~110km from Essex); corrected to its real location (`51.6335, 0.8143`)
- `spirit-of-scotland-travelpass`: NR's page doesn't expose pricing (renders via a calculator widget); manually supplied as £155 (4 in 8 days) / £196 (8 in 15 days)
- Transport for Greater Manchester Rail Ranger (GM1) and Day Saver (GM2/GM3/GM4): missing entirely from NR's ranger-rover promotions listing (they live under `ticket-types/tickets/` instead, e.g. `/ticket-types/tickets/gm1/`); added manually via `add_tickets` with their station coverage sourced from [TfGM's train station list](https://tfgm.com/ways-to-travel/train/stations) (97 stations, names matched to NR's `coords.json` regional-disambiguation conventions, e.g. "Eccles (Manchester)" not "Eccles"). GM2/GM3/GM4 are really one ticket product (train+bus / train+tram / train+bus+tram variants of the same Day Saver) sharing one NR content page, so they're represented as a single entry. Neither ticket exposes pricing on NR's site (calculator widget, as with Spirit of Scotland) -- not yet supplied
- `sy-connect` (SY Connect+): present on NR's site as a real promotion page, but under a different `promotionType` than the ranger-rover listing `fetch_tickets.py` scrapes, so it's added via `add_tickets` instead of appearing automatically. Its 29-station South Yorkshire TravelMaster zone (cross-checked against [Travel South Yorkshire's rail station list](https://www.travelsouthyorkshire.com/en-gb/populardestinations/rail-stations)) plus Denby Dale -- which the ticket genuinely covers despite being outside the official zone and a West Yorkshire station -- are supplied directly in the override entry; pricing (£12.20/day) was supplied manually as more current than the £10.70 NR's own scrape currently shows
- `day-save-southern` (DaySave - Southern): same situation as `sy-connect` -- a real NR promotion page (`day-save-southern`) that sits outside the scraped ranger-rover listing, so it's added via `add_tickets`. Unlike the other manually-added tickets, NR's page for this one does embed a full 98-station coverage zone and pricing, both lifted directly from its `__NEXT_DATA__` (station names already match `coords.json`'s conventions, e.g. "Earlswood Surrey", "Seaford Sussex"); pricing is £26 for one adult (SD1) or £52 for a group of 3-4 adults (SD4) -- up to four accompanying children travel for £2 each but there's no standalone child fare, so that's noted in the description rather than forced into the `childPrice` field

## Data sources

- Ticket data: [National Rail promotions](https://www.nationalrail.co.uk/ticket-types/promotions/) via embedded `__NEXT_DATA__` JSON
- Station coordinates: [National Rail's station directory](https://www.nationalrail.co.uk/stations/) — `fetch_coords.py` reads NR's `sitemap-stations.xml` to enumerate every station page (2,611 of the 2,612 listed; one brand-new station has no published location yet), then reads each one's exact lat/lon from its detail page (`/stations/{slug}/`). This is NR's own authoritative data, already disambiguated by region (e.g. "Whitchurch (Cardiff)" vs "Whitchurch (Shropshire)" each get their own correct coordinates rather than colliding on a shared base name, which an earlier OpenStreetMap-based approach was prone to). Because this covers the *entire* network rather than just stations referenced by name in ticket data, `build_html.py` also uses it to populate the station list for tickets NR marks as covering all stations (e.g. All Line Rover) explicitly, rather than leaving their coverage area blank

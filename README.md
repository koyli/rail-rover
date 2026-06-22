# Rail Rover & Ranger Map

Interactive map of all UK rail rover and ranger tickets, sourced from [National Rail](https://www.nationalrail.co.uk/ticket-types/promotions/?promotionType=ranger-rover).
Interactive map of places reachable in a day (one way or return) per rover ticket (rail-explorer).

**Live:** https://koyli.github.io/rail-rover/ and  https://koyli.github.io/rail-rover/rail-explorer/

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
  rover_prices.json # Ticket id -> pricing[] — output of fetch_rover_prices.py
scripts/
  fetch_tickets.py       # Scrapes NR website for all tickets (reads __NEXT_DATA__ JSON)
  fetch_coords.py        # Fetches station coordinates from OSM Overpass API
  download_fares.sh      # Downloads the RSP Fares Data Feed (fares.zip) -- needs NRDP credentials
  fetch_rover_prices.py  # Extracts current rover/ranger pricing from the feed's Rail Rovers file
  build_html.py          # Generates index.html from the data files
```

## Rebuilding from scratch

```bash
# 1. Scrape all tickets from National Rail (~95 tickets, takes ~30s)
python3 scripts/fetch_tickets.py

# 2. Fetch station coordinates for the entire National Rail network (~2,610 stations, takes ~8min)
python3 scripts/fetch_coords.py

# 3. (Optional) Get current rover/ranger pricing from the RSP Fares Data Feed
NRDP_USERNAME=you@example.com OUTPUT_FILE=data/fares.zip ./scripts/download_fares.sh
unzip -o data/fares.zip -d data/fares
python3 scripts/fetch_rover_prices.py

# 4. Generate index.html
python3 scripts/build_html.py
```

The scripts write to `data/` and read `overrides.json` automatically.

## Rover/ranger pricing from the RSP Fares Data Feed

`fetch_rover_prices.py` parses the feed's Rail Rovers file (`RJFAFnnn.TRR`,
record types `R`/`P` -- RSP spec RSPS5045 02-00 section 4.12) and matches its
~750 rover/ranger products against `tickets_raw.json` by name. For matches
scoring ≥0.85 (~57 of the 95 tickets, mostly day rangers and named multi-day
rovers), it writes the current adult/child standard-class fare (no railcard)
to `data/rover_prices.json`. `build_html.py` applies this as each matched
ticket's pricing, taking precedence over the NR scrape -- but
`overrides.json`'s `pricing` block, if present for the same ticket id, still
wins over both (e.g. `spirit-of-scotland-travelpass`, which has two pricing
tiers the feed match doesn't capture).

Requires `data/fares/RJFAF*.TRR`, which isn't checked in (the full feed is
~300MB); regenerate it via `download_fares.sh` + unzip as shown above.

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
- `northern-explorer`: a brand-new product (on sale from 8 June 2026, found via the RSP fares feed's `FN1-4`/`GN1-4`/`HN1-4`/`IN1-4` codes), too new to appear in NR's scraped promotions listing, so added via `add_tickets`. Covers all 533 stations on [Northern's own station list](https://www.northernrailway.co.uk/stations) (all matched `coords.json` directly, no renaming needed) -- a different, larger network than the existing `northern-explorer-55-west-day-ranger`. Pricing is a 4x4 grid of 1-4 days x 1-4 people travelling together (£35-£390), taken from NR's promotion page and cross-checked against the feed; the 50% child discount NR describes only applies to the 1-person fares, so `childPrice` is only set on those four entries
- `c-2-c-senior-rover`, `cheshire-day-ranger`, `lancashire-day-ranger`, `north-east-round-robin`, `north-west-round-robin`, `tyne-tees-day-ranger`, `freedom-of-north-east-7-days`, `freedom-of-north-east-4-in-8-days`: all genuine, on-sale tickets whose detail pages are still live with full station/pricing data, but which dropped out of NR's scraped ranger-rover listing sometime between the 2026-06-07 and 2026-06-21 refreshes (NR's listing endpoint appears to silently omit tickets from time to time, independent of whether they're still sold). Re-added via `add_tickets` using their own page data, same as the other listing-omission cases above. Worth re-checking on future refreshes in case NR's listing starts including them again (which would make the `add_tickets` entry redundant rather than wrong, since `fetch_tickets.py` would just produce a duplicate `id`)

- `cumbria-day-ranger`, `cumbrian-coast-day-ranger`, `cumbria-round-robin`, `hadrians-wall-country-line-day-ranger`, `settle-carlisle-line-day-ranger`: dropped from NR's scraped listing between 2026-06-07 and 2026-06-21, and their NR promotion pages now 200 but as CMS stubs (`promotion` object missing `pricingCollection`/sale dates entirely, unlike a normal live ticket). Re-added anyway via `add_tickets` on 2026-06-22, because the RSP fares feed (`data/fares/RJFAF*.TRR`) tells a different story: each still has an open-ended (`31122999`) `R` validity record with no end date, i.e. they're still defined as current, on-sale rovers in the actual retail fares system -- NR appears to have pulled the marketing pages while leaving the underlying products live. Station lists and pricing are each ticket's own last-known-good data from the 2026-06-07 scrape (`cumbria-day-ranger` £58.20/£29.10, `cumbrian-coast-day-ranger` £26.40/£13.20, `cumbria-round-robin` £40.80/£20.40, `hadrians-wall-country-line-day-ranger` £28.30/£14.15), cross-checked against the feed's fallback price for each code's current period (the feed leaves a NO_FARE sentinel for the brand-new period until the next fares revision publishes a real price -- see the comment on `current_price()` in `fetch_rover_prices.py` -- so it falls back to the same figures). `settle-carlisle-line-day-ranger` never had a station list from NR at all (`stations: []` even when it was still scraped); its 27-station Leeds/Bradford Forster Square-to-Carlisle route via Skipton, Settle and Appleby comes from NR's generic `/ticket-types/tickets/scu/` page description plus independent route sources, and supersedes (consolidates into `add_tickets` directly) a pre-existing `add_stations` override of the identical list.
- `cumbria-travel-pass-1-day`, `cumbria-travel-pass-3-day`: Northern's new replacement product for the five Cumbria/border rangers above, launched 18 June 2026 (ticket codes `LF1`/`CT3`). Too new for NR's scraped listing, but its `/ticket-types/tickets/lf1/`/`/ticket-types/tickets/ct3/` pages disclose the actual rail validity: "Northern, Avanti West Coast and TransPennine Express services between Lancaster and Carlisle (via Barrow-in-Furness), Lancaster and Carlisle (via Penrith), and Oxenholme and Windermere" -- which maps station-for-station onto the same network the old Cumbria Round Robin covered, so its 42-station list is reused for both Travel Pass variants. Pricing (£40.00/£20.00 child for 1 day, £99.00/£49.50 child for 3 day) taken directly from the feed's `LF1`/`CT3` records (a real, current-period price in this case, not a fallback), matching the figures reported by [RailAdvent](https://www.railadvent.co.uk/2026/06/northern-launch-new-cumbria-travel-pass.html). Not yet added: `lakes-day-ranger`, the sixth ticket dropped in the same window -- same situation as the others, but out of scope for now

## Data sources

- Ticket data: [National Rail promotions](https://www.nationalrail.co.uk/ticket-types/promotions/) via embedded `__NEXT_DATA__` JSON
- Station coordinates: [National Rail's station directory](https://www.nationalrail.co.uk/stations/) — `fetch_coords.py` reads NR's `sitemap-stations.xml` to enumerate every station page (2,611 of the 2,612 listed; one brand-new station has no published location yet), then reads each one's exact lat/lon from its detail page (`/stations/{slug}/`). This is NR's own authoritative data, already disambiguated by region (e.g. "Whitchurch (Cardiff)" vs "Whitchurch (Shropshire)" each get their own correct coordinates rather than colliding on a shared base name, which an earlier OpenStreetMap-based approach was prone to). Because this covers the *entire* network rather than just stations referenced by name in ticket data, `build_html.py` also uses it to populate the station list for tickets NR marks as covering all stations (e.g. All Line Rover) explicitly, rather than leaving their coverage area blank

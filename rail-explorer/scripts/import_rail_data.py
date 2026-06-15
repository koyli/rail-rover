#!/usr/bin/env python3
"""
Import ticket + station data from the parent rail-rover repo, applying the
same overrides.json corrections build_html.py does (add_tickets,
remove_stations, add_stations, station_coords), so rail-explorer's ticket
station lists match what rail-rover's map shows.

Output: data/tickets.json (id, name, operator, stations[]),
        data/coords.json  (station name -> [lat, lon], only stations used
                            by at least one ticket)
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
RAIL_ROVER = ROOT.parent

TICKETS_FILE = RAIL_ROVER / "data" / "tickets_raw.json"
COORDS_FILE = RAIL_ROVER / "data" / "coords.json"
OVERRIDES_FILE = RAIL_ROVER / "overrides.json"

OUT_TICKETS = ROOT / "data" / "tickets.json"
OUT_COORDS = ROOT / "data" / "coords.json"


def main():
    tickets = json.load(open(TICKETS_FILE))
    coords = json.load(open(COORDS_FILE))
    overrides = json.load(open(OVERRIDES_FILE))

    for new_ticket in overrides.get("add_tickets", []):
        new_ticket.setdefault("stations", [])
        new_ticket.setdefault("stations_complete", True)
        new_ticket.setdefault("applies_to_all_stations", False)
        tickets.append(new_ticket)

    for t in tickets:
        t["name"] = re.sub(r"  +", " ", t["name"]).strip()

    all_station_names = sorted(coords)
    for t in tickets:
        if t.get("applies_to_all_stations") and not t["stations"]:
            t["stations"] = all_station_names

    remove_map = overrides.get("remove_stations", {})
    for t in tickets:
        removals = remove_map.get(t["id"], [])
        if removals:
            t["stations"] = [s for s in t["stations"] if s not in removals]

    add_map = overrides.get("add_stations", {})
    for t in tickets:
        additions = add_map.get(t["id"], [])
        if additions:
            existing = set(t["stations"])
            t["stations"] = t["stations"] + [s for s in additions if s not in existing]

    for name, coord in overrides.get("station_coords", {}).items():
        coords[name] = coord

    out_tickets = []
    used_stations = set()
    for t in tickets:
        stations = sorted(set(t["stations"]))
        out_tickets.append({
            "id": t["id"],
            "name": t["name"],
            "operator": t.get("operator", ""),
            "stations": stations,
        })
        used_stations.update(stations)

    out_coords = {name: coords[name] for name in used_stations if name in coords}

    missing = used_stations - set(out_coords)
    if missing:
        print(f"WARNING: {len(missing)} station(s) used by tickets have no coordinates: "
              f"{sorted(missing)}")

    OUT_TICKETS.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_TICKETS, "w") as f:
        json.dump(out_tickets, f, indent=2, ensure_ascii=False)
    with open(OUT_COORDS, "w") as f:
        json.dump(out_coords, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(out_tickets)} tickets to {OUT_TICKETS}")
    print(f"Wrote {len(out_coords)} station coordinates to {OUT_COORDS}")


if __name__ == "__main__":
    main()

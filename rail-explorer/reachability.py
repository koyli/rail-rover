#!/usr/bin/env python3
"""
Earliest-arrival reachability over the CIF-derived timetable database
(data/timetable.db), restricted to a single ticket's covered stations.

Given a ticket, a starting station, a date and a start time, returns the
earliest arrival time (and number of train changes) at every other station
on the ticket that can be reached that day.

Simplifications (v1):
- Pathfinding is restricted to stations on the ticket's coverage list --
  trains are assumed usable between any two covered stations regardless of
  operator (most rover/ranger tickets are valid on any TOC within their
  geographic zone).
- One-way reachability only, capped at the end of the start date
  (no overnight/next-day continuation).
- No minimum interchange/connection time -- a same-minute change is
  considered valid.
- Train splitting/joining (CIF associations) is not modelled.
"""

import heapq
import json
import sqlite3
from datetime import date as Date
from pathlib import Path

ROOT = Path(__file__).parent
DB_FILE = ROOT / "data" / "timetable.db"
TICKETS_FILE = ROOT / "data" / "tickets.json"
STATION_CRS_FILE = ROOT / "data" / "station_crs.json"
TOC_OPERATORS_FILE = ROOT / "data" / "toc_operators.json"

END_OF_DAY = 24 * 60  # minutes


def _time_to_minutes(t):
    """CIF time field 'HHMM' or 'HHMMH' (H = half-minute) -> minutes."""
    if not t:
        return None
    minutes = int(t[0:2]) * 60 + int(t[2:4])
    return minutes


def _load_tickets():
    return {t["id"]: t for t in json.load(open(TICKETS_FILE))}


def _load_station_crs():
    return json.load(open(STATION_CRS_FILE))


def _load_toc_operators():
    data = json.load(open(TOC_OPERATORS_FILE))
    data.pop("_comment", None)
    return data


def _allowed_tocs(ticket, toc_operators):
    """
    Return the set of CIF TOC codes permitted by this ticket's `operator`
    field, or None if the ticket places no operator restriction (empty
    `operator` field, e.g. Interrail passes or "AnyTrain" tickets).
    """
    if not ticket.get("operator"):
        return None
    allowed_names = set(ticket["operator"].split(", "))
    return {
        toc for toc, names in toc_operators.items()
        if allowed_names & set(names)
    }


def reachable(ticket_id, start_station, date_str, start_time_str, mode="one-way", conn=None):
    """
    Returns {station_name: {"arrival": minutes_since_midnight, "changes": int, ...}}
    for every ticket station reachable from start_station on date_str
    (YYYY-MM-DD) departing no earlier than start_time_str (HH:MM).

    mode:
      "one-way" (default) -- any station reachable by end of day.
      "return" -- only stations from which a return journey to
        start_station, arriving by end of day, is also possible. Each
        result also includes "return_by": the latest minute at which a
        train back towards the start station must be caught.
    """
    if mode not in ("one-way", "return"):
        raise ValueError(f"Unknown mode: {mode}")
    tickets = _load_tickets()
    if ticket_id not in tickets:
        raise ValueError(f"Unknown ticket: {ticket_id}")
    ticket = tickets[ticket_id]

    station_crs = _load_station_crs()

    # station name <-> CRS, restricted to this ticket's stations
    name_to_crs = {}
    crs_to_name = {}
    for name in ticket["stations"]:
        crs = station_crs.get(name)
        if crs:
            name_to_crs[name] = crs
            crs_to_name.setdefault(crs, name)

    if start_station not in name_to_crs:
        raise ValueError(f"Station {start_station!r} not on ticket {ticket_id} "
                          f"(or has no timetable data)")

    start_crs = name_to_crs[start_station]
    start_minutes = int(start_time_str[0:2]) * 60 + int(start_time_str[3:5])

    year, month, day = (int(x) for x in date_str.split("-"))
    weekday = Date(year, month, day).weekday()  # 0=Mon .. 6=Sun
    date_compact = date_str.replace("-", "")

    toc_operators = _load_toc_operators()
    allowed_tocs = _allowed_tocs(ticket, toc_operators)

    # Bit-per-station mask of "restricted pair" members visited on the
    # current path; a transition that would set both bits of any pair is
    # rejected, since the ticket isn't valid for travel between them
    # (directly, or via a route that passes through both).
    restricted_pairs = [
        (name_to_crs[a], name_to_crs[b])
        for a, b in ticket.get("restricted_pairs", [])
        if a in name_to_crs and b in name_to_crs
    ]
    restricted_crs = sorted({c for pair in restricted_pairs for c in pair})
    bit_of = {c: i for i, c in enumerate(restricted_crs)}
    pair_bits = [(bit_of[a], bit_of[b]) for a, b in restricted_pairs]

    def mask_of(crs):
        bit = bit_of.get(crs)
        return (1 << bit) if bit is not None else 0

    def violates(mask):
        return any((mask & (1 << a)) and (mask & (1 << b)) for a, b in pair_bits)

    start_mask = mask_of(start_crs)
    start_key = (start_crs, start_mask)

    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(DB_FILE)

    try:
        graph = _build_graph(conn, date_compact, weekday, set(crs_to_name), allowed_tocs)

        # earliest-arrival Dijkstra; second key is number of distinct trains
        # taken so far (1 for the first train, +1 each time the uid changes)
        best = {start_key: (start_minutes, 0)}  # (crs, mask) -> (arrival, trains)
        prev_uid = {start_key: None}
        prev_edge = {}  # (crs, mask) -> (from_crs, dep_time, arr_time, uid, from_mask)
        pq = [(start_minutes, 0, start_crs, start_mask)]
        while pq:
            t, trains, crs, mask = heapq.heappop(pq)
            key = (crs, mask)
            if t > best[key][0] or (t == best[key][0] and trains > best[key][1]):
                continue
            for dep_time, arr_time, dest, uid in graph.get(crs, []):
                if dep_time < t or arr_time > END_OF_DAY:
                    continue
                new_mask = mask | mask_of(dest)
                if violates(new_mask):
                    continue
                new_trains = trains if uid == prev_uid.get(key) else trains + 1
                dest_key = (dest, new_mask)
                cur = best.get(dest_key)
                if cur is None or arr_time < cur[0] or (arr_time == cur[0] and new_trains < cur[1]):
                    best[dest_key] = (arr_time, new_trains)
                    prev_uid[dest_key] = uid
                    prev_edge[dest_key] = (crs, dep_time, arr_time, uid, mask)
                    heapq.heappush(pq, (arr_time, new_trains, dest, new_mask))

        if mode == "return":
            return_g, return_prev_edge = _latest_return(graph, start_crs, start_mask, mask_of, violates)
        else:
            return_g = return_prev_edge = None

        # Group reached states by station, ignoring the start station.
        by_station = {}  # crs -> [(mask, (arrival, trains)), ...]
        for (crs, mask), v in best.items():
            if crs == start_crs:
                continue
            by_station.setdefault(crs, []).append((mask, v))

        return_by_station = None
        if mode == "return":
            return_by_station = {}
            for (crs, mask), dep in return_g.items():
                return_by_station.setdefault(crs, []).append((mask, dep))

        result = {}
        for crs, states in by_station.items():
            name = crs_to_name.get(crs)
            if not name:
                continue

            if mode == "return":
                # Visiting both members of a restricted pair across the
                # whole round trip (outbound + return) is disallowed, even
                # if neither leg alone would violate it.
                rstates = return_by_station.get(crs, [])
                chosen = None
                for m1, (arrival, trains) in states:
                    for m2, return_by in rstates:
                        if violates(m1 | m2):
                            continue
                        if arrival > return_by:
                            continue
                        if chosen is None or arrival < chosen[0] or (arrival == chosen[0] and trains < chosen[1]):
                            chosen = (arrival, trains, return_by, m1, m2)
                if chosen is None:
                    continue
                arrival, trains, return_by, m1, m2 = chosen
                entry = {"arrival": arrival, "changes": max(trains - 1, 0), "return_by": return_by}
                entry["return_itinerary"] = _reconstruct_return_itinerary(
                    return_prev_edge, crs_to_name, start_key, (crs, m2))
                entry["itinerary"] = _reconstruct_itinerary(prev_edge, crs_to_name, start_key, (crs, m1))
            else:
                chosen = None
                for mask, (arrival, trains) in states:
                    if chosen is None or arrival < chosen[0] or (arrival == chosen[0] and trains < chosen[1]):
                        chosen = (arrival, trains, mask)
                arrival, trains, mask = chosen
                entry = {"arrival": arrival, "changes": max(trains - 1, 0)}
                entry["itinerary"] = _reconstruct_itinerary(prev_edge, crs_to_name, start_key, (crs, mask))

            result[name] = entry
        return result
    finally:
        if own_conn:
            conn.close()


def _collapse_legs(edges, crs_to_name):
    """
    Given a list of (from_crs, dep_time, to_crs, arr_time, uid) edges in
    travel order, collapse consecutive edges on the same train (uid) into
    a single leg and return
    [{"from": name, "dep": minutes, "to": name, "arr": minutes}, ...]
    """
    legs = []
    for from_crs, dep_time, to_crs, arr_time, uid in edges:
        if legs and legs[-1]["_uid"] == uid:
            legs[-1]["to"] = to_crs
            legs[-1]["arr"] = arr_time
        else:
            legs.append({"from": from_crs, "dep": dep_time, "to": to_crs, "arr": arr_time, "_uid": uid})

    for leg in legs:
        leg["from"] = crs_to_name.get(leg["from"], leg["from"])
        leg["to"] = crs_to_name.get(leg["to"], leg["to"])
        del leg["_uid"]
    return legs


def _reconstruct_itinerary(prev_edge, crs_to_name, start_key, dest_key):
    """
    Walk prev_edge back from dest_key to start_key and return the
    collapsed outbound itinerary in travel order (start -> dest).

    Keys are (crs, mask) pairs (see `reachable`'s restricted-pair masking).
    """
    edges = []  # (from_crs, dep_time, to_crs, arr_time, uid), dest -> start order
    node = dest_key
    while node != start_key:
        from_crs, dep_time, arr_time, uid, from_mask = prev_edge[node]
        edges.append((from_crs, dep_time, node[0], arr_time, uid))
        node = (from_crs, from_mask)
    edges.reverse()
    return _collapse_legs(edges, crs_to_name)


def _reconstruct_return_itinerary(prev_edge_back, crs_to_name, start_key, dest_key):
    """
    Walk prev_edge_back forward from dest_key to start_key and return the
    collapsed return itinerary in travel order (dest -> start).

    Keys are (crs, mask) pairs (see `reachable`'s restricted-pair masking).
    """
    edges = []  # (from_crs, dep_time, to_crs, arr_time, uid), dest -> start order
    node = dest_key
    while node != start_key:
        next_crs, dep_time, arr_time, uid, next_mask = prev_edge_back[node]
        edges.append((node[0], dep_time, next_crs, arr_time, uid))
        node = (next_crs, next_mask)
    return _collapse_legs(edges, crs_to_name)


def _latest_return(graph, start_crs, start_mask, mask_of, violates):
    """
    For each (crs, mask) state, the latest minute at which a homeward train
    (one that eventually leads back to start_crs by END_OF_DAY) departs
    that station, and the next hop taken on that homeward journey. Computed
    by a "latest arrival" search over the reversed graph, seeded with
    start_crs reachable at END_OF_DAY (being there is always fine).

    `mask` tracks restricted-pair stations visited along the path back to
    start_crs (see `reachable`); a transition that would set both bits of a
    restricted pair is rejected via `violates`.

    Returns (g, prev_edge_back) where g[(crs, mask)] is the latest
    departure minute and prev_edge_back[(crs, mask)] =
    (next_crs, dep_time, arr_time, uid, next_mask) is the edge taken from
    crs towards start_crs.
    """
    reverse_graph = {}
    for src, edges in graph.items():
        for dep_time, arr_time, dest, uid in edges:
            reverse_graph.setdefault(dest, []).append((arr_time, dep_time, src, uid))

    start_key = (start_crs, start_mask)
    g = {start_key: END_OF_DAY}
    prev_edge_back = {}
    pq = [(-END_OF_DAY, start_crs, start_mask)]
    while pq:
        neg_g, crs, mask = heapq.heappop(pq)
        gx = -neg_g
        key = (crs, mask)
        if gx < g.get(key, -1):
            continue
        for arr_time, dep_time, src, uid in reverse_graph.get(crs, []):
            new_mask = mask | mask_of(src)
            if violates(new_mask):
                continue
            dest_key = (src, new_mask)
            if arr_time <= gx and dep_time > g.get(dest_key, -1):
                g[dest_key] = dep_time
                prev_edge_back[dest_key] = (crs, dep_time, arr_time, uid, mask)
                heapq.heappush(pq, (-dep_time, src, new_mask))
    return g, prev_edge_back


def _build_graph(conn, date_compact, weekday, crs_filter, allowed_tocs=None):
    """
    Build {crs: [(dep_time, arr_time, dest_crs, uid), ...]} for all schedules
    active on the given date, restricted to stops whose CRS is in crs_filter.

    If allowed_tocs is not None, schedules operated by a TOC outside that
    set are excluded entirely.
    """
    rows = conn.execute(
        "SELECT id, uid, stp_indicator, toc FROM schedules "
        "WHERE start_date <= ? AND end_date >= ? AND substr(days_run, ?, 1) = '1'",
        (date_compact, date_compact, weekday + 1),
    ).fetchall()

    # resolve STP overrides per uid: C cancels, O/N overrides P
    by_uid = {}
    for sid, uid, stp, toc in rows:
        by_uid.setdefault(uid, []).append((sid, stp, toc))

    active_ids = []
    for uid, variants in by_uid.items():
        stps = {stp for _, stp, _ in variants}
        if "C" in stps:
            continue
        chosen = None
        for sid, stp, toc in variants:
            if stp in ("O", "N"):
                chosen = (sid, toc)
                break
        if chosen is None:
            for sid, stp, toc in variants:
                if stp == "P":
                    chosen = (sid, toc)
                    break
        if chosen is not None:
            sid, toc = chosen
            if allowed_tocs is not None and toc not in allowed_tocs:
                continue
            active_ids.append((sid, uid))

    # TIPLOC -> CRS lookup
    tiploc_crs = dict(conn.execute("SELECT tiploc, crs FROM tiplocs WHERE crs != ''"))

    graph = {}
    for sid, uid in active_ids:
        stops = conn.execute(
            "SELECT tiploc, arr, dep FROM stop_times WHERE schedule_id = ? ORDER BY seq",
            (sid,),
        ).fetchall()

        # Walk the stops in order, tracking a running day-offset: CIF times
        # reset to 00xx after midnight, so any time earlier than the
        # previous one indicates the schedule has rolled over into the
        # next day.
        covered = []
        offset = 0
        prev_time = None
        for tiploc, arr, dep in stops:
            a = _time_to_minutes(arr)
            d = _time_to_minutes(dep)
            for t in (a, d):
                if t is None:
                    continue
                adj = t + offset
                if prev_time is not None and adj < prev_time:
                    offset += 1440
                    adj = t + offset
                prev_time = adj
            crs = tiploc_crs.get(tiploc)
            if crs in crs_filter:
                covered.append((
                    crs,
                    a + offset if a is not None else None,
                    d + offset if d is not None else None,
                ))

        for (crs_a, _, dep_a), (crs_b, arr_b, _) in zip(covered, covered[1:]):
            if dep_a is None or arr_b is None or crs_a == crs_b:
                continue
            graph.setdefault(crs_a, []).append((dep_a, arr_b, crs_b, uid))

    for edges in graph.values():
        edges.sort()
    return graph

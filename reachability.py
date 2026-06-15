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

    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(DB_FILE)

    try:
        graph = _build_graph(conn, date_compact, weekday, set(crs_to_name), allowed_tocs)

        # earliest-arrival Dijkstra; second key is number of distinct trains
        # taken so far (1 for the first train, +1 each time the uid changes)
        best = {start_crs: (start_minutes, 0)}  # crs -> (arrival, trains)
        prev_uid = {start_crs: None}
        prev_edge = {start_crs: None}  # crs -> (from_crs, dep_time, arr_time, uid)
        pq = [(start_minutes, 0, start_crs)]
        while pq:
            t, trains, crs = heapq.heappop(pq)
            if t > best[crs][0] or (t == best[crs][0] and trains > best[crs][1]):
                continue
            for dep_time, arr_time, dest, uid in graph.get(crs, []):
                if dep_time < t or arr_time > END_OF_DAY:
                    continue
                new_trains = trains if uid == prev_uid.get(crs) else trains + 1
                cur = best.get(dest)
                if cur is None or arr_time < cur[0] or (arr_time == cur[0] and new_trains < cur[1]):
                    best[dest] = (arr_time, new_trains)
                    prev_uid[dest] = uid
                    prev_edge[dest] = (crs, dep_time, arr_time, uid)
                    heapq.heappush(pq, (arr_time, new_trains, dest))

        if mode == "return":
            return_by, return_prev_edge = _latest_return(graph, start_crs)
        else:
            return_by = return_prev_edge = None

        result = {}
        for crs, (arrival, trains) in best.items():
            if crs == start_crs:
                continue
            if mode == "return":
                deadline = return_by.get(crs)
                if deadline is None or arrival > deadline:
                    continue
            name = crs_to_name.get(crs)
            if name:
                entry = {"arrival": arrival, "changes": max(trains - 1, 0)}
                if mode == "return":
                    entry["return_by"] = return_by[crs]
                    entry["return_itinerary"] = _reconstruct_return_itinerary(
                        return_prev_edge, crs_to_name, start_crs, crs)
                entry["itinerary"] = _reconstruct_itinerary(prev_edge, crs_to_name, start_crs, crs)
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


def _reconstruct_itinerary(prev_edge, crs_to_name, start_crs, dest_crs):
    """
    Walk prev_edge back from dest_crs to start_crs and return the
    collapsed outbound itinerary in travel order (start -> dest).
    """
    edges = []  # (from_crs, dep_time, to_crs, arr_time, uid), dest -> start order
    node = dest_crs
    while node != start_crs:
        from_crs, dep_time, arr_time, uid = prev_edge[node]
        edges.append((from_crs, dep_time, node, arr_time, uid))
        node = from_crs
    edges.reverse()
    return _collapse_legs(edges, crs_to_name)


def _reconstruct_return_itinerary(prev_edge_back, crs_to_name, start_crs, dest_crs):
    """
    Walk prev_edge_back forward from dest_crs to start_crs and return the
    collapsed return itinerary in travel order (dest -> start).
    """
    edges = []  # (from_crs, dep_time, to_crs, arr_time, uid), dest -> start order
    node = dest_crs
    while node != start_crs:
        next_crs, dep_time, arr_time, uid = prev_edge_back[node]
        edges.append((node, dep_time, next_crs, arr_time, uid))
        node = next_crs
    return _collapse_legs(edges, crs_to_name)


def _latest_return(graph, start_crs):
    """
    For each crs, the latest minute at which a homeward train (one that
    eventually leads back to start_crs by END_OF_DAY) departs that
    station, and the next hop taken on that homeward journey. Computed by
    a "latest arrival" search over the reversed graph, seeded with
    start_crs reachable at END_OF_DAY (being there is always fine).

    Returns (g, prev_edge_back) where g[crs] is the latest departure
    minute and prev_edge_back[crs] = (next_crs, dep_time, arr_time, uid)
    is the edge taken from crs towards start_crs.
    """
    reverse_graph = {}
    for src, edges in graph.items():
        for dep_time, arr_time, dest, uid in edges:
            reverse_graph.setdefault(dest, []).append((arr_time, dep_time, src, uid))

    g = {start_crs: END_OF_DAY}
    prev_edge_back = {}
    pq = [(-END_OF_DAY, start_crs)]
    while pq:
        neg_g, crs = heapq.heappop(pq)
        gx = -neg_g
        if gx < g.get(crs, -1):
            continue
        for arr_time, dep_time, src, uid in reverse_graph.get(crs, []):
            if arr_time <= gx and dep_time > g.get(src, -1):
                g[src] = dep_time
                prev_edge_back[src] = (crs, dep_time, arr_time, uid)
                heapq.heappush(pq, (-dep_time, src))
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

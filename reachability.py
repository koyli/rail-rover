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


def reachable(ticket_id, start_station, date_str, start_time_str, conn=None):
    """
    Returns {station_name: {"arrival": minutes_since_midnight, "changes": int}}
    for every ticket station reachable from start_station on date_str
    (YYYY-MM-DD) departing no earlier than start_time_str (HH:MM).
    """
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

    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(DB_FILE)

    try:
        graph = _build_graph(conn, date_compact, weekday, set(crs_to_name))

        # earliest-arrival Dijkstra; second key is number of distinct trains
        # taken so far (1 for the first train, +1 each time the uid changes)
        best = {start_crs: (start_minutes, 0)}  # crs -> (arrival, trains)
        prev_uid = {start_crs: None}
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
                    heapq.heappush(pq, (arr_time, new_trains, dest))

        result = {}
        for crs, (arrival, trains) in best.items():
            if crs == start_crs:
                continue
            name = crs_to_name.get(crs)
            if name:
                result[name] = {"arrival": arrival, "changes": max(trains - 1, 0)}
        return result
    finally:
        if own_conn:
            conn.close()


def _build_graph(conn, date_compact, weekday, crs_filter):
    """
    Build {crs: [(dep_time, arr_time, dest_crs, uid), ...]} for all schedules
    active on the given date, restricted to stops whose CRS is in crs_filter.
    """
    rows = conn.execute(
        "SELECT id, uid, stp_indicator FROM schedules "
        "WHERE start_date <= ? AND end_date >= ? AND substr(days_run, ?, 1) = '1'",
        (date_compact, date_compact, weekday + 1),
    ).fetchall()

    # resolve STP overrides per uid: C cancels, O/N overrides P
    by_uid = {}
    for sid, uid, stp in rows:
        by_uid.setdefault(uid, []).append((sid, stp))

    active_ids = []
    for uid, variants in by_uid.items():
        stps = {stp for _, stp in variants}
        if "C" in stps:
            continue
        chosen = None
        for sid, stp in variants:
            if stp in ("O", "N"):
                chosen = sid
                break
        if chosen is None:
            for sid, stp in variants:
                if stp == "P":
                    chosen = sid
                    break
        if chosen is not None:
            active_ids.append((chosen, uid))

    # TIPLOC -> CRS lookup
    tiploc_crs = dict(conn.execute("SELECT tiploc, crs FROM tiplocs WHERE crs != ''"))

    graph = {}
    for sid, uid in active_ids:
        stops = conn.execute(
            "SELECT tiploc, arr, dep FROM stop_times WHERE schedule_id = ? ORDER BY seq",
            (sid,),
        ).fetchall()

        covered = []
        for tiploc, arr, dep in stops:
            crs = tiploc_crs.get(tiploc)
            if crs in crs_filter:
                covered.append((crs, _time_to_minutes(arr), _time_to_minutes(dep)))

        for (crs_a, _, dep_a), (crs_b, arr_b, _) in zip(covered, covered[1:]):
            if dep_a is None or arr_b is None or crs_a == crs_b:
                continue
            graph.setdefault(crs_a, []).append((dep_a, arr_b, crs_b, uid))

    for edges in graph.values():
        edges.sort()
    return graph

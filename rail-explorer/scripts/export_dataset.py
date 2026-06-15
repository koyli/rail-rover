#!/usr/bin/env python3
"""
Export data/timetable.db into a compact, dictionary-coded JSON document that
the browser can download and run reachability queries against directly
(see reachability.js).

For every passenger schedule active on some date, this:
  - resolves the day-rollover offsets the same way reachability.py's
    _build_graph does (CIF times reset to 00xx after midnight), producing
    arrival/departure times in minutes-since-start-of-schedule-day, possibly
    >= 1440 for next-day stops;
  - joins TIPLOC -> CRS;
  - keeps only stops at a CRS used by at least one ticket (the "universal"
    station set -- per-ticket filtering happens client-side).

Schedules with fewer than 2 such stops (no usable edge) are dropped.

Output: data/timetable_export.json (gzipped to .gz alongside it)

{
  "generated": "YYYY-MM-DD",
  "epoch": "YYYY-MM-DD",            // day 0 for startDay/endDay
  "crs":   ["LDS", "MAN", ...],     // index -> CRS code
  "tocs":  ["NT", "GW", ...],       // index -> CIF TOC code
  "uids":  ["C12345", ...],         // index -> train UID
  "schedules": [
    [uidIdx, startDay, endDay, daysRunMask, stpCode, tocIdx,
      [[crsIdx, arr, dep], ...]],   // arr/dep in minutes, -1 if absent
    ...
  ]
}

stpCode: 0=P, 1=O, 2=N, 3=C
daysRunMask: bit i set => runs on weekday i (0=Monday .. 6=Sunday)
"""

import gzip
import json
import sqlite3
from datetime import date as Date
from pathlib import Path

ROOT = Path(__file__).parent.parent
DB_FILE = ROOT / "data" / "timetable.db"
TICKETS_FILE = ROOT / "data" / "tickets.json"
STATION_CRS_FILE = ROOT / "data" / "station_crs.json"
OUTPUT = ROOT / "data" / "timetable_export.json"

EPOCH = Date(2020, 1, 1)
STP_CODES = {"P": 0, "O": 1, "N": 2, "C": 3}


def _time_to_minutes(t):
    if not t:
        return None
    return int(t[0:2]) * 60 + int(t[2:4])


def _universal_crs():
    tickets = json.load(open(TICKETS_FILE))
    station_crs = json.load(open(STATION_CRS_FILE))
    crs = set()
    for t in tickets:
        for name in t["stations"]:
            c = station_crs.get(name)
            if c:
                crs.add(c)
    return crs


def main():
    universal_crs = _universal_crs()
    print(f"Universal CRS set: {len(universal_crs)} stations")

    conn = sqlite3.connect(DB_FILE)
    tiploc_crs = dict(conn.execute("SELECT tiploc, crs FROM tiplocs WHERE crs != ''"))

    crs_list = sorted(universal_crs)
    crs_index = {c: i for i, c in enumerate(crs_list)}

    uid_index = {}
    toc_index = {}
    schedules_out = []

    rows = conn.execute(
        "SELECT id, uid, stp_indicator, start_date, end_date, days_run, toc FROM schedules"
    )
    n_total = 0
    n_kept = 0
    for sid, uid, stp, start_date, end_date, days_run, toc in rows:
        n_total += 1
        stops = conn.execute(
            "SELECT tiploc, arr, dep FROM stop_times WHERE schedule_id = ? ORDER BY seq",
            (sid,),
        ).fetchall()

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
            if crs in crs_index:
                covered.append((
                    crs_index[crs],
                    a + offset if a is not None else -1,
                    d + offset if d is not None else -1,
                ))

        if len(covered) < 2:
            continue

        uid_idx = uid_index.setdefault(uid, len(uid_index))
        toc_idx = toc_index.setdefault(toc, len(toc_index))

        sy, sm, sd = int(start_date[0:4]), int(start_date[4:6]), int(start_date[6:8])
        ey, em, ed = int(end_date[0:4]), int(end_date[4:6]), int(end_date[6:8])
        start_day = (Date(sy, sm, sd) - EPOCH).days
        end_day = (Date(ey, em, ed) - EPOCH).days

        days_run_mask = 0
        for i, c in enumerate(days_run):
            if c == "1":
                days_run_mask |= 1 << i

        schedules_out.append([
            uid_idx, start_day, end_day, days_run_mask, STP_CODES[stp], toc_idx,
            [list(c) for c in covered],
        ])
        n_kept += 1

    conn.close()

    uid_list = [None] * len(uid_index)
    for uid, i in uid_index.items():
        uid_list[i] = uid
    toc_list = [None] * len(toc_index)
    for toc, i in toc_index.items():
        toc_list[i] = toc

    data = {
        "generated": Date.today().isoformat(),
        "epoch": EPOCH.isoformat(),
        "crs": crs_list,
        "tocs": toc_list,
        "uids": uid_list,
        "schedules": schedules_out,
    }

    with open(OUTPUT, "w") as f:
        json.dump(data, f, separators=(",", ":"))

    gz_path = OUTPUT.with_suffix(OUTPUT.suffix + ".gz")
    with open(OUTPUT, "rb") as f_in, gzip.open(gz_path, "wb", compresslevel=9) as f_out:
        f_out.write(f_in.read())

    print(f"Schedules: {n_total} total, {n_kept} kept")
    print(f"Wrote {OUTPUT} ({OUTPUT.stat().st_size / 1e6:.1f} MB)")
    print(f"Wrote {gz_path} ({gz_path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Parse the National Rail CIF timetable (data/cif/RJTTF*.MCA, 80-char fixed
width records) into a SQLite database for reachability queries.

Tables:
  tiplocs(tiploc, crs, description)
      from TI records -- TIPLOC -> CRS code + station description, used to
      cross-reference against rail-rover's station names.
  schedules(id, uid, stp_indicator, start_date, end_date, days_run, toc, category)
      from BS/BX records -- one row per schedule "version"; multiple rows
      can share a uid (permanent + overlays + cancellations), resolved at
      query time by STP indicator.
  stop_times(schedule_id, seq, tiploc, arr, dep)
      from LO/LI/LT records -- only rows with a real arrival or departure
      time (pure passing points are dropped).

Field positions reverse-engineered from a real RJTTF*.MCA extract; see
https://wiki.openraildata.com/index.php/CIF_File_Format for the spec.
"""

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
CIF_DIR = ROOT / "data" / "cif"
DB_FILE = ROOT / "data" / "timetable.db"

# Passenger-ish train categories: O=ordinary passenger, X=express passenger,
# B=bus. Everything else (blank, freight, etc.) is dropped.
PASSENGER_CATEGORY_PREFIXES = ("O", "X", "B")


def cif_date(s):
    """YYMMDD -> YYYYMMDD (all CIF dates are in the 20xx range)."""
    return "20" + s


def find_mca():
    candidates = sorted(CIF_DIR.glob("*.MCA")) + sorted(CIF_DIR.glob("*.mca"))
    if not candidates:
        sys.exit(f"No .MCA file found in {CIF_DIR} -- run download_timetable.py first")
    return candidates[0]


def main():
    mca_path = find_mca()
    print(f"Parsing {mca_path} ({mca_path.stat().st_size / 1e6:.0f} MB)...")

    if DB_FILE.exists():
        DB_FILE.unlink()
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA journal_mode = MEMORY")

    conn.execute("""
        CREATE TABLE tiplocs (
            tiploc TEXT PRIMARY KEY,
            crs TEXT,
            description TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE schedules (
            id INTEGER PRIMARY KEY,
            uid TEXT,
            stp_indicator TEXT,
            start_date TEXT,
            end_date TEXT,
            days_run TEXT,
            toc TEXT,
            category TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE stop_times (
            schedule_id INTEGER,
            seq INTEGER,
            tiploc TEXT,
            arr TEXT,
            dep TEXT
        )
    """)

    tiplocs = []
    schedules = []
    stop_times = []

    n_schedules_kept = 0
    n_schedules_seen = 0
    current_schedule_id = None
    current_seq = 0
    pending_schedule = None  # (id, uid, stp, start, end, days_run, category), toc not yet seen

    BATCH = 50000

    def flush_pending_schedule(toc=""):
        nonlocal pending_schedule
        if pending_schedule is not None:
            sid, uid, stp, start, end, days_run, category = pending_schedule
            schedules.append((sid, uid, stp, start, end, days_run, toc, category))
            pending_schedule = None

    def flush():
        if tiplocs:
            conn.executemany("INSERT OR IGNORE INTO tiplocs VALUES (?,?,?)", tiplocs)
            tiplocs.clear()
        if schedules:
            conn.executemany("INSERT INTO schedules VALUES (?,?,?,?,?,?,?,?)", schedules)
            schedules.clear()
        if stop_times:
            conn.executemany("INSERT INTO stop_times VALUES (?,?,?,?,?)", stop_times)
            stop_times.clear()

    with open(mca_path, encoding="latin-1") as f:
        for line in f:
            rt = line[0:2]

            if rt == "TI":
                tiploc = line[2:9].strip()
                crs = line[53:56].strip()
                description = line[18:44].strip()
                tiplocs.append((tiploc, crs, description))

            elif rt == "BS":
                flush_pending_schedule()
                n_schedules_seen += 1
                category = line[30:32]
                if category[0:1] in PASSENGER_CATEGORY_PREFIXES:
                    current_schedule_id = n_schedules_seen
                    current_seq = 0
                    pending_schedule = (
                        current_schedule_id,
                        line[3:9],            # uid
                        line[79],             # stp indicator
                        cif_date(line[9:15]),  # start_date
                        cif_date(line[15:21]),  # end_date
                        line[21:28],          # days_run
                        category,
                    )
                    n_schedules_kept += 1
                else:
                    current_schedule_id = None

            elif rt == "BX" and current_schedule_id is not None:
                flush_pending_schedule(toc=line[11:13])

            elif rt in ("LO", "LI", "LT") and current_schedule_id is not None:
                flush_pending_schedule()
                tiploc = line[2:9].strip()
                if rt == "LO":
                    arr, dep = "", line[10:15].strip()
                elif rt == "LT":
                    arr, dep = line[10:15].strip(), ""
                else:  # LI
                    arr, dep = line[10:15].strip(), line[15:20].strip()

                if arr or dep:
                    current_seq += 1
                    stop_times.append((current_schedule_id, current_seq, tiploc, arr, dep))

            if len(tiplocs) + len(schedules) + len(stop_times) >= BATCH:
                flush()

    flush_pending_schedule()
    flush()
    conn.commit()

    print(f"Schedules seen: {n_schedules_seen}, kept (passenger): {n_schedules_kept}")

    print("Indexing...")
    conn.execute("CREATE INDEX idx_schedules_uid ON schedules(uid)")
    conn.execute("CREATE INDEX idx_schedules_dates ON schedules(start_date, end_date)")
    conn.execute("CREATE INDEX idx_stop_times_schedule ON stop_times(schedule_id)")
    conn.execute("CREATE INDEX idx_stop_times_tiploc ON stop_times(tiploc)")
    conn.execute("CREATE INDEX idx_tiplocs_crs ON tiplocs(crs)")
    conn.commit()

    counts = {}
    for table in ("tiplocs", "schedules", "stop_times"):
        counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f"Wrote {DB_FILE}: {counts}")

    conn.close()


if __name__ == "__main__":
    main()

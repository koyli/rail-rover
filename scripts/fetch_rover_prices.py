#!/usr/bin/env python3
"""
Extract current rover/ranger pricing from the RSP Fares Data Feed's Rail
Rovers file (.TRR, record types 'R'/'P' -- see RSPS5045 02-00 section 4.12)
and match it against data/tickets_raw.json by name.

Requires data/fares/RJFAF*.TRR, obtained via:
  scripts/download_fares.sh   (produces fares.zip)
  unzip fares.zip -d data/fares

Outputs: data/rover_prices.json -- {ticket_id: pricing[]}, in the same shape
as the "pricing" field in tickets_raw.json / overrides.json, for tickets
matched with high confidence. build_html.py applies this as a pricing
source, after the NR scrape but before overrides.json's "pricing" overrides
(so a manual override can still take precedence if needed).

Only matches scoring >= MATCH_THRESHOLD against a normalised ticket name are
used; everything else is left for overrides.json or the NR scrape.
"""

import json, re, sys, difflib
from pathlib import Path
from datetime import date

ROOT = Path(__file__).parent.parent
TICKETS_FILE = ROOT / "data" / "tickets_raw.json"
OUTPUT = ROOT / "data" / "rover_prices.json"
TODAY = date.today()

MATCH_THRESHOLD = 0.85

STOPWORDS = r"\b(THE|RAILROVER|RAIL|ROVER|RANGER|TICKET|DAY|DAYS|IN|A|AND|OF|&)\b"

# Tickets in overrides.json's add_tickets that were re-added on the strength
# of this feed still showing them on sale, even though National Rail's own
# website dropped them from its listing (see README.md). If a code below
# stops resolving to a current record, or starts describing something else
# (a feed code can be reused for an unrelated product once retired -- LF1
# went from "LAKES DAY RANGER" to "CUMBRIA 1 DAY TRAVEL PASS" this way, which
# is why lakes-day-ranger isn't tracked here), that's the signal the ticket
# is genuinely gone and should be removed from overrides.json.
TRACKED_DISCONTINUED_TICKETS = {
    "CRR": ("cumbria-round-robin", "Cumbria Round Robin"),
    "CAM": ("cumbria-day-ranger", "Cumbria Day Ranger"),
    "CUC": ("cumbrian-coast-day-ranger", "Cumbrian Coast Day Ranger"),
    "TV1": ("hadrians-wall-country-line-day-ranger", "Hadrians Wall Country Line Day Ranger"),
    "SCU": ("settle-carlisle-line-day-ranger", "Settle & Carlisle Line Day Ranger"),
    "LF1": ("cumbria-travel-pass-1-day", "Cumbria Travel Pass (1 Day)"),
    "CT3": ("cumbria-travel-pass-3-day", "Cumbria Travel Pass (3 Day)"),
}
DESCRIPTION_DRIFT_THRESHOLD = 0.5


def parse_date(s):
    return date(int(s[4:8]), int(s[2:4]), int(s[0:2]))


def parse_trr(path):
    rovers = {}
    with open(path) as f:
        for line in f:
            if line.startswith("/"):
                continue
            rtype, code, end_date = line[0], line[1:4], line[4:12]
            entry = rovers.setdefault(code, {"records": [], "prices": []})
            if rtype == "R":
                entry["records"].append({
                    "end_date": end_date,
                    "start_date": line[12:20],
                    "description": line[28:58].strip(),
                })
            elif rtype == "P":
                entry["prices"].append({
                    "end_date": end_date,
                    "railcard": line[12:15].strip(),
                    "class": line[15],
                    "adult_pence": int(line[16:24]),
                    "child_pence": int(line[24:32]),
                })
    return rovers


def current_record(records):
    """Pick the record valid as of today, preferring one with no end date."""
    candidates = [
        r for r in records
        if parse_date(r["start_date"]) <= TODAY
        and (r["end_date"] == "31122999" or parse_date(r["end_date"]) >= TODAY)
    ]
    if not candidates:
        return None
    for r in candidates:
        if r["end_date"] == "31122999":
            return r
    return max(candidates, key=lambda r: r["start_date"])


NO_FARE = 99999999


def current_price(prices):
    """Most recently-published standard-class adult fare with no railcard.

    A price record's ADULT_FARE can be the sentinel NO_FARE (99999999) for
    the rover's current validity period if the next fares revision hasn't
    set a price yet, even though the rover itself is still on sale -- in
    that case fall back to the latest period that does have a real price.
    """
    candidates = [p for p in prices if p["railcard"] == "" and p["class"] == "2" and p["adult_pence"] != NO_FARE]
    if not candidates:
        return None
    return max(candidates, key=lambda p: date.max if p["end_date"] == "31122999" else parse_date(p["end_date"]))


def normalise(name):
    name = name.upper()
    name = name.replace("DAYSIN", "DAYS IN")
    # split adjacent digit/letter runs (e.g. "3DAYS" / "IN7") so day-count
    # patterns like "3 in 7" are comparable regardless of source spacing
    name = re.sub(r"(\d)([A-Z])", r"\1 \2", name)
    name = re.sub(r"([A-Z])(\d)", r"\1 \2", name)
    name = re.sub(r"[^A-Z0-9 ]", " ", name)
    name = re.sub(STOPWORDS, " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def digits(name):
    return tuple(re.findall(r"\d+", name))


def main():
    trr_arg = sys.argv[1] if len(sys.argv) > 1 else None
    if trr_arg:
        trr_path = Path(trr_arg)
    else:
        candidates = sorted((ROOT / "data" / "fares").glob("RJFAF*.TRR"))
        if not candidates:
            sys.exit("No RJFAF*.TRR file found in data/fares/ "
                     "(run download_fares.sh and unzip fares.zip into data/fares/)")
        trr_path = candidates[-1]

    rovers = parse_trr(trr_path)

    current = {}
    for code, entry in rovers.items():
        rec = current_record(entry["records"])
        if not rec:
            continue
        price = current_price(entry["prices"])
        if not price:
            continue
        current[code] = {**rec, "price": price}

    tickets = json.load(open(TICKETS_FILE))

    # best ticket match seen for each feed rover code, regardless of whether
    # it clears MATCH_THRESHOLD -- used below to report feed rovers with no
    # corresponding ticket on the map at all (vs. just a fuzzy name mismatch)
    code_best = {}

    result = {}
    for t in tickets:
        norm_name = normalise(t["name"])
        name_digits = digits(norm_name)

        scored = []
        for code, rec in current.items():
            norm_desc = normalise(rec["description"])
            score = difflib.SequenceMatcher(None, norm_name, norm_desc).ratio()
            scored.append((score, code, rec, digits(norm_desc)))
            if code not in code_best or score > code_best[code][0]:
                code_best[code] = (score, t["id"], t["name"])

        # If the ticket name contains a day-count pattern (e.g. "3 in 7"),
        # prefer candidates whose description has the exact same digits --
        # otherwise "3 in 7" and "7 day" variants of the same rover can be
        # confused, since they differ by only one short token
        if name_digits:
            exact = [s for s in scored if s[3] == name_digits]
            if exact:
                scored = exact

        best = max(scored, key=lambda s: s[0]) if scored else None
        if not best or best[0] < MATCH_THRESHOLD:
            continue
        score, code, rec, _ = best
        price = rec["price"]
        entry = {"adultPrice": round(price["adult_pence"] / 100, 2)}
        if price["child_pence"] != NO_FARE:
            entry["childPrice"] = round(price["child_pence"] / 100, 2)
        result[t["id"]] = [entry]
        print(f"{score:.2f} {t['id']:<45} {t['name']:<42} <-> {code} {rec['description']}")

    with open(OUTPUT, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\nMatched {len(result)}/{len(tickets)} tickets (threshold {MATCH_THRESHOLD}); written to {OUTPUT}")

    unmatched = sorted(
        (code for code in current if code_best.get(code, (0,))[0] < MATCH_THRESHOLD),
        key=lambda code: -code_best.get(code, (0,))[0],
    )
    print(f"\n{len(unmatched)} fares-feed rover(s) currently on sale with no ticket match "
          f">= {MATCH_THRESHOLD}:")
    for code in unmatched:
        score, tid, tname = code_best.get(code, (0, None, None))
        closest = f"closest: {score:.2f} {tname} ({tid})" if tid else "no tickets to compare against"
        print(f"  {code} {current[code]['description']:<30} {closest}")

    print(f"\nChecking {len(TRACKED_DISCONTINUED_TICKETS)} ticket(s) kept in overrides.json on the "
          f"strength of this feed:")
    for code, (ticket_id, ticket_name) in TRACKED_DISCONTINUED_TICKETS.items():
        rec = current_record(rovers.get(code, {}).get("records", []))
        if rec is None:
            print(f"::warning::Feed code {code} (overrides.json ticket {ticket_id!r}, "
                  f"{ticket_name!r}) no longer has a current validity record -- it may be "
                  f"genuinely discontinued now. Check whether to remove it from "
                  f"overrides.json's add_tickets.")
            continue
        score = difflib.SequenceMatcher(None, normalise(ticket_name), normalise(rec["description"])).ratio()
        if score < DESCRIPTION_DRIFT_THRESHOLD:
            print(f"::warning::Feed code {code} now describes {rec['description']!r}, not "
                  f"{ticket_name!r} (overrides.json ticket {ticket_id!r}) -- the code may have "
                  f"been reused for a different product. Check overrides.json.")
            continue
        print(f"  OK: {code} ({rec['description']}) still current -> {ticket_id}")


if __name__ == "__main__":
    main()

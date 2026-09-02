#!/usr/bin/env python3
"""
Fetch all rover/ranger tickets from National Rail.

The NR website is a Next.js app. All data (including full station lists,
bypassing the "Show more" toggle) is embedded in __NEXT_DATA__ JSON.

Outputs: data/tickets_raw.json
"""

import subprocess, json, re, time, sys
from pathlib import Path

BASE_URL = "https://www.nationalrail.co.uk"
LISTING_URL = BASE_URL + "/ticket-types/promotions/?promotionType=ranger-rover&page={page}"
LISTING_DATA_URL = BASE_URL + "/_next/data/{build_id}/tickets-railcards-and-offers/ticket-types/promotions.json?promotionType=ranger-rover&page={page}"
DETAIL_URL  = BASE_URL + "/tickets-railcards-offers/promotions/{slug}/"

OUTPUT = Path(__file__).parent.parent / "data" / "tickets_raw.json"


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def fetch_html(url):
    r = subprocess.run(
        ["curl", "-s", "-L", "--max-time", "15", "-A", USER_AGENT, url],
        capture_output=True, text=True,
    )
    return r.stdout


def extract_next_data(html):
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
    if not m:
        return None
    return json.loads(m.group(1))


def rich_text(node):
    if not node:
        return ""
    if isinstance(node, dict):
        if node.get("nodeType") == "text":
            return node.get("value", "")
        return " ".join(rich_text(c) for c in node.get("content", [])).strip()
    return ""


def get_stations(promo):
    stations = set()
    for field in ["applicableZoneOfStationsCollection",
                  "applicableOriginStationsCollection",
                  "applicableDestinationStationsCollection"]:
        for item in (promo.get(field) or {}).get("items", []):
            name = item.get("name", "").strip()
            if name:
                stations.add(name)
    return sorted(stations)


def get_pricing(promo):
    items = (promo.get("pricingCollection") or {}).get("items", [])
    result = []
    for p in items:
        label = rich_text((p.get("validityNotes") or {}).get("json", {})).strip() or None
        entry = {"label": label}
        for key in ["adultPrice", "childPrice", "concessionPrice", "familyPrice", "groupPrice"]:
            v = p.get(key)
            if v is not None:
                entry[key] = v
        rc_items = (p.get("railcardDiscountPricesCollection") or {}).get("items", [])
        if rc_items:
            discounts = []
            for rc in rc_items:
                cards = [c["railcardName"] for c in (rc.get("railcardsCollection") or {}).get("items", []) if c.get("railcardName")]
                price = rc.get("priceWithRailcard")
                if price is not None:
                    discounts.append({"railcards": cards, "price": price})
            if discounts:
                entry["railcardPrices"] = discounts
        result.append(entry)
    return result


def collect_slugs():
    # The HTML listing route's getServerSideProps ignores ?page (always returns
    # page 1) since NR's site redesign; its own Next.js data-fetch JSON endpoint
    # (used for client-side pagination) still honours it, so page 1 is fetched
    # as HTML to discover the current buildId and every page after that goes
    # through the JSON endpoint.
    #
    # Whatever this returns is necessarily a snapshot, not the full picture:
    # NR's listing backend has been observed to omit ~15 genuine ranger/rover
    # tickets from one scrape and include them (with others missing instead)
    # in another taken later the same day -- stable for minutes at a time,
    # confirmed to shift over roughly an hour, with no practical way to force
    # or wait out a refresh from here. main() compensates by reconciling
    # against the previously-committed ticket list rather than trusting a
    # single pass to be complete.
    page1 = extract_next_data(fetch_html(LISTING_URL.format(page=1)))
    if not page1:
        return []
    build_id = page1["buildId"]

    slugs = []
    seen = set()
    data = page1
    for page in range(1, 20):
        results = data["props"]["pageProps"].get("allPromotionResults", [])
        if not results:
            break
        for p in results:
            # The promotionType=ranger-rover query param is itself a bit
            # loose (it lets some railcards/fare products through), and NR's
            # listing occasionally repeats an entry across pages; re-check
            # the type and de-dupe here rather than trust either.
            if p.get("promotionType", {}).get("promotionTypeCode") != "RangerRover":
                continue
            if p["slug"] not in seen:
                seen.add(p["slug"])
                slugs.append(p["slug"])
        print(f"  Page {page}: {len(results)} tickets", flush=True)
        time.sleep(0.5)
        raw = json.loads(fetch_html(LISTING_DATA_URL.format(build_id=build_id, page=page + 1)))
        data = {"props": {"pageProps": raw["pageProps"]}}
    return slugs


def previously_committed_slugs():
    result = subprocess.run(
        ["git", "-C", str(OUTPUT.parent.parent), "show", "HEAD:data/tickets_raw.json"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return set()
    return {t["id"] for t in json.loads(result.stdout)}


def fetch_ticket(slug):
    html = fetch_html(DETAIL_URL.format(slug=slug))
    data = extract_next_data(html)
    if not data:
        return None
    promo = data.get("props", {}).get("pageProps", {}).get("promotion", {})
    if not promo:
        return None

    operators = [o["operatorName"] for o in
                 (promo.get("applicableOperatorsCollection") or {}).get("items", [])
                 if o.get("operatorName")]

    return {
        "id": slug,
        "name": promo.get("promotionName", ""),
        "url": DETAIL_URL.format(slug=slug),
        "description": rich_text((promo.get("summary") or {}).get("json", {})),
        "operator": ", ".join(operators),
        "stations": get_stations(promo),
        "stations_complete": True,
        "applies_to_all_stations": promo.get("appliesToAllStations", False),
        "validity_map_url": promo.get("areaMap") or None,
        "pricing": get_pricing(promo),
    }


def main():
    print("Collecting ticket slugs...")
    slugs = collect_slugs()
    print(f"Listing returned: {len(slugs)} tickets")

    # The listing is a flaky snapshot (see collect_slugs' docstring): a
    # ticket present last time this ran but absent from today's listing
    # might just be a listing gap, not a real removal. Rather than trust
    # this run's listing alone, also re-check every previously-committed
    # ticket id that didn't show up in it -- keep it if its detail page is
    # still live, drop it (it's genuinely gone) otherwise.
    recheck = sorted(previously_committed_slugs() - set(slugs))
    if recheck:
        print(f"Re-checking {len(recheck)} previously-known ticket(s) missing from this listing: {recheck}\n")
    slugs = slugs + recheck

    tickets = []
    for i, slug in enumerate(slugs):
        ticket = fetch_ticket(slug)
        if not ticket:
            print(f"[{i+1}/{len(slugs)}] {slug}: FAILED (no longer live -- dropped)", flush=True)
            continue
        adult = next((p.get("adultPrice") for p in ticket["pricing"] if p.get("adultPrice")), None)
        print(f"[{i+1}/{len(slugs)}] {ticket['name']}: {len(ticket['stations'])} stations"
              + (f", adult=£{adult}" if adult else ""), flush=True)
        tickets.append(ticket)
        time.sleep(0.25)

    OUTPUT.parent.mkdir(exist_ok=True)
    with open(OUTPUT, "w") as f:
        json.dump(tickets, f, indent=2, ensure_ascii=False)
    print(f"\nSaved {len(tickets)} tickets to {OUTPUT}")


if __name__ == "__main__":
    main()

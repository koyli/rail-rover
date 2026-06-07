#!/usr/bin/env python3
"""
Generate index.html from:
  - data/tickets_raw.json  (scraped ticket + station + pricing data)
  - data/coords.json       (station name -> [lat, lon])
  - overrides.json         (manual corrections to NR data errors)

Run after fetch_tickets.py and fetch_coords.py.
"""

import json, re, colorsys
from pathlib import Path

ROOT          = Path(__file__).parent.parent
TICKETS_FILE  = ROOT / "data" / "tickets_raw.json"
COORDS_FILE   = ROOT / "data" / "coords.json"
OVERRIDES_FILE = ROOT / "overrides.json"
OUTPUT        = ROOT / "index.html"

LEAFLET_VERSION = "1.9.4"
LEAFLET_JS_SRI  = "sha384-cxOPjt7s7Iz04uaHJceBmS+qpjv2JkIHNVcuOrM+YHwZOmJGBXI00mdUXEq65HTH"
LEAFLET_CSS_SRI = "sha384-sHL9NAb7lN7rfvG5lfHpm643Xkcjzp4jFvuavGOndn6pjVqS6ny56CAt3nsEVT4H"


def gen_colors(n):
    colors = []
    params = [
        (0.85, 0.55), (0.70, 0.45), (0.60, 0.65), (0.90, 0.40),
        (0.50, 0.55), (0.80, 0.70), (1.00, 0.50),
    ]
    per_band = (n + len(params) - 1) // len(params)
    for sat, lig in params:
        for i in range(per_band):
            hue = (i / per_band + params.index((sat, lig)) * 0.15) % 1.0
            r, g, b = colorsys.hls_to_rgb(hue, lig, sat)
            colors.append("#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255)))
    return colors[:n]


def main():
    with open(TICKETS_FILE) as f:
        tickets = json.load(f)
    with open(COORDS_FILE) as f:
        coords = json.load(f)
    with open(OVERRIDES_FILE) as f:
        overrides = json.load(f)

    # Clean up names
    for t in tickets:
        t["name"] = re.sub(r"  +", " ", t["name"]).strip()

    # Apply overrides
    remove_map = overrides.get("remove_stations", {})
    for t in tickets:
        removals = remove_map.get(t["id"], [])
        if removals:
            before = len(t["stations"])
            t["stations"] = [s for s in t["stations"] if s not in removals]
            print(f"Override {t['name']!r}: removed {before - len(t['stations'])} station(s)")

    # Some tickets' NR pages don't list their stations at all (e.g. Settle &
    # Carlisle Line Day Ranger has an empty `stations` array and only a PDF
    # route map) -- supply the missing ones manually. Names must match
    # coords.json exactly (e.g. "Appleby" vs a disambiguated regional variant)
    # or they simply won't have a map marker
    add_map = overrides.get("add_stations", {})
    for t in tickets:
        additions = add_map.get(t["id"], [])
        if additions:
            existing = set(t["stations"])
            new = [s for s in additions if s not in existing]
            t["stations"] = t["stations"] + new
            print(f"Override {t['name']!r}: added {len(new)} station(s)")
            unknown = [s for s in new if s not in coords]
            if unknown:
                print(f"  WARNING: {len(unknown)} added station(s) have no coordinates "
                      f"(check spelling against coords.json): {unknown}")

    # NR's own station pages occasionally have wrong coordinates (e.g.
    # Burnham-on-Crouch is published in the North Sea, ~110km from its
    # actual location) -- correct those here rather than in coords.json,
    # since fetch_coords.py would silently overwrite a direct edit there
    for name, coord in overrides.get("station_coords", {}).items():
        if name in coords and coords[name] != coord:
            print(f"Override station coordinates {name!r}: {coords[name]} -> {coord}")
        coords[name] = coord

    # Some tickets' NR pages don't expose pricing in the page data (e.g. it's
    # rendered via an interactive calculator widget) -- supply it manually
    pricing_map = overrides.get("pricing", {})
    for t in tickets:
        if t["id"] in pricing_map:
            t["pricing"] = pricing_map[t["id"]]
            print(f"Override pricing for {t['name']!r}: {len(t['pricing'])} price tier(s) supplied manually")

    # Tickets that cover the whole network (e.g. All Line Rover) come from NR
    # with an empty `stations` array plus an applies_to_all_stations flag --
    # list every station in the network explicitly instead, which is more
    # accurate for the map than relying on the flag alone
    all_station_names = sorted(coords)
    for t in tickets:
        if t.get("applies_to_all_stations") and not t["stations"]:
            t["stations"] = all_station_names
            print(f"{t['name']!r}: populated with all {len(all_station_names)} network stations")

    # Sort alphabetically for the sidebar legend
    tickets.sort(key=lambda t: t["name"].lower())

    colors = gen_colors(len(tickets))
    COLORS = {t["id"]: colors[i] for i, t in enumerate(tickets)}

    used_stations = {s for t in tickets for s in t["stations"]}
    used_coords   = {s: coords[s] for s in used_stations if s in coords}

    missing = used_stations - set(used_coords)
    if missing:
        print(f"WARNING: {len(missing)} stations have no coordinates and will not appear on map:")
        for s in sorted(missing):
            print(f"  {s}")

    tickets_js = json.dumps(tickets, ensure_ascii=False)
    colors_js  = json.dumps(COLORS,  ensure_ascii=False)
    coords_js  = json.dumps(used_coords, ensure_ascii=False)

    lv = LEAFLET_VERSION
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Rail Rover &amp; Ranger Map</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@{lv}/dist/leaflet.css" integrity="{LEAFLET_CSS_SRI}" crossorigin="anonymous"/>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ display: flex; height: 100vh; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #1a1a2e; color: #e0e0e0; overflow: hidden; }}
    #sidebar {{ width: 310px; min-width: 310px; display: flex; flex-direction: column; background: #16213e; border-right: 1px solid #0f3460; }}
    #sidebar-header {{ padding: 14px 16px; background: #0f3460; }}
    #sidebar-header h1 {{ font-size: 14px; font-weight: 700; color: #fff; }}
    #sidebar-header p {{ font-size: 11px; color: #90a8c0; margin-top: 3px; line-height: 1.4; }}
    #sidebar-controls {{ padding: 8px 16px; display: flex; gap: 8px; border-bottom: 1px solid #0f3460; }}
    .ctrl-btn {{ flex: 1; padding: 5px 0; border: 1px solid #1a4a8a; background: #1a2a4a; color: #90b8d8; font-size: 11px; cursor: pointer; border-radius: 3px; }}
    .ctrl-btn:hover {{ background: #1a3a6a; color: #fff; }}
    #ticket-list {{ flex: 1; overflow-y: auto; padding: 4px 0; }}
    #ticket-list::-webkit-scrollbar {{ width: 5px; }}
    #ticket-list::-webkit-scrollbar-thumb {{ background: #0f3460; border-radius: 3px; }}
    .ticket-item {{ display: flex; align-items: flex-start; padding: 7px 16px; cursor: pointer; border-left: 3px solid transparent; gap: 10px; transition: background 0.12s; position: relative; }}
    .ticket-item:hover {{ background: #1e2e50; }}
    .ticket-item.active {{ background: #1a3060; }}
    .ticket-swatch {{ width: 13px; height: 13px; min-width: 13px; border-radius: 50%; margin-top: 2px; border: 2px solid rgba(255,255,255,0.15); transition: transform 0.12s; }}
    .ticket-item.active .ticket-swatch {{ transform: scale(1.25); border-color: rgba(255,255,255,0.5); }}
    .ticket-info {{ flex: 1; min-width: 0; }}
    .ticket-name {{ font-size: 11.5px; font-weight: 600; color: #c8d8e8; line-height: 1.3; }}
    .ticket-item.active .ticket-name {{ color: #fff; }}
    .ticket-meta {{ font-size: 10px; color: #506070; margin-top: 2px; }}
    .ticket-item.active .ticket-meta {{ color: #7090a8; }}
    .ticket-link {{ display: inline-flex; align-items: center; justify-content: center; margin-top: 1px; opacity: 0.4; transition: opacity 0.12s; color: #90b8d8; text-decoration: none; flex-shrink: 0; }}
    .ticket-item:hover .ticket-link {{ opacity: 0.85; }}
    .ticket-link:hover {{ opacity: 1 !important; color: #fff; }}
    .price-tooltip {{ display: none; position: absolute; left: 318px; top: 0; z-index: 2000; background: rgba(10,20,50,0.97); border: 1px solid #1a4a8a; border-radius: 6px; padding: 9px 12px; min-width: 180px; max-width: 260px; font-size: 10.5px; pointer-events: none; box-shadow: 0 4px 16px rgba(0,0,0,0.6); white-space: nowrap; }}
    .ticket-item:hover .price-tooltip {{ display: block; }}
    .price-tooltip-title {{ font-size: 11px; font-weight: 700; color: #fff; margin-bottom: 6px; border-bottom: 1px solid #1a4a8a; padding-bottom: 5px; }}
    .price-row {{ display: flex; justify-content: space-between; gap: 14px; margin-bottom: 3px; color: #90b8d8; }}
    .price-row .plabel {{ color: #6080a0; }}
    .price-row .pval {{ color: #c8e8ff; font-weight: 600; }}
    .price-section-head {{ font-size: 9.5px; color: #506070; text-transform: uppercase; letter-spacing: 0.05em; margin: 5px 0 3px; }}
    .price-rc-row {{ color: #6080a0; font-size: 10px; margin-bottom: 2px; display: flex; justify-content: space-between; gap: 10px; }}
    .price-rc-row .pval {{ color: #a0c8e8; font-weight: 600; }}
    #map-wrap {{ flex: 1; position: relative; }}
    #map {{ width: 100%; height: 100%; }}
    #hover-panel {{ position: absolute; bottom: 24px; right: 12px; z-index: 1000; background: rgba(15,30,60,0.96); border: 1px solid #1a4a8a; border-radius: 6px; padding: 10px 14px; max-width: 280px; font-size: 11px; pointer-events: none; display: none; box-shadow: 0 4px 16px rgba(0,0,0,0.5); }}
    #hover-panel h3 {{ font-size: 12.5px; font-weight: 700; color: #fff; margin-bottom: 7px; }}
    .hover-ticket {{ display: flex; align-items: center; gap: 6px; margin-bottom: 3px; }}
    .hover-dot {{ width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }}
    .hover-ticket-name {{ color: #b0c8e0; font-size: 10.5px; }}
    .hover-ticket-price {{ color: #7090a8; font-size: 10px; margin-left: auto; padding-left: 8px; white-space: nowrap; }}
    #hint {{ position: absolute; top: 10px; left: 50%; transform: translateX(-50%); background: rgba(15,30,60,0.88); border: 1px solid #1a4a8a; border-radius: 20px; padding: 4px 14px; font-size: 10.5px; color: #7090a8; pointer-events: none; z-index: 1000; white-space: nowrap; }}
    .leaflet-tooltip {{ background: #0f1e3c !important; border: 1px solid #1a4a8a !important; color: #c0d8f0 !important; font-size: 11px !important; padding: 3px 8px !important; border-radius: 3px !important; box-shadow: 0 2px 6px rgba(0,0,0,0.4) !important; }}
    .leaflet-tooltip::before {{ border-top-color: #1a4a8a !important; }}
  </style>
</head>
<body>
<div id="sidebar">
  <div id="sidebar-header">
    <h1>Rail Rovers &amp; Rangers</h1>
    <p>Toggle tickets to highlight coverage. Hover stations to see which tickets apply.</p>
  </div>
  <div id="sidebar-controls">
    <button class="ctrl-btn" onclick="selectAll()">Select All</button>
    <button class="ctrl-btn" onclick="clearAll()">Clear All</button>
  </div>
  <div id="ticket-list"></div>
</div>
<div id="map-wrap">
  <div id="map"></div>
  <div id="hint">Hover a station marker to see applicable tickets</div>
  <div id="hover-panel"><h3 id="hover-name"></h3><div id="hover-tickets"></div></div>
</div>
<script src="https://unpkg.com/leaflet@{lv}/dist/leaflet.js" integrity="{LEAFLET_JS_SRI}" crossorigin="anonymous"></script>
<script>
const TICKETS = {tickets_js};
const COLORS  = {colors_js};
const COORDS  = {coords_js};

function fmtPrice(p) {{
  if (p == null) return null;
  return '£' + (Number.isInteger(p) ? p : p.toFixed(2));
}}

function coverageLabel(ticket) {{
  const n = ticket.stations.length;
  if (n) return n + ' stations';
  if (ticket.applies_to_all_stations) return 'network-wide';
  if (ticket.validity_map_url) return 'see route map';
  return 'coverage not listed';
}}

function priceSummary(ticket) {{
  if (!ticket.pricing || !ticket.pricing.length) return null;
  const prices = ticket.pricing.map(p => p.adultPrice).filter(p => p != null);
  if (!prices.length) return null;
  const min = Math.min(...prices), max = Math.max(...prices);
  // Some tickets (e.g. Spirit of Scotland) have multiple genuinely different
  // fares -- show the full span rather than just the first one, so the
  // headline number isn't mistaken for "the" price
  return min === max ? fmtPrice(min) : `${{fmtPrice(min)}}–${{fmtPrice(max)}}`;
}}

function buildPriceTooltip(ticket) {{
  if (!ticket.pricing || !ticket.pricing.length) return '<div class="price-tooltip"><div class="price-tooltip-title">Prices</div><div style="color:#506070;font-size:10px">No pricing data available</div></div>';
  let html = '<div class="price-tooltip"><div class="price-tooltip-title">Prices</div>';
  for (const p of ticket.pricing) {{
    if (p.label) html += `<div class="price-section-head">${{p.label}}</div>`;
    const rows = [['Adult', p.adultPrice], ['Child', p.childPrice], ['Concession', p.concessionPrice], ['Family', p.familyPrice], ['Group', p.groupPrice]];
    for (const [lbl, val] of rows) {{
      if (val != null) html += `<div class="price-row"><span class="plabel">${{lbl}}</span><span class="pval">${{fmtPrice(val)}}</span></div>`;
    }}
    if (p.railcardPrices && p.railcardPrices.length) {{
      html += '<div class="price-section-head">With Railcard</div>';
      for (const rc of p.railcardPrices) {{
        const cards = rc.railcards && rc.railcards.length ? rc.railcards.join(', ') : 'Railcard';
        html += `<div class="price-rc-row"><span>${{cards}}</span><span class="pval">${{fmtPrice(rc.price)}}</span></div>`;
      }}
    }}
  }}
  html += '</div>';
  return html;
}}

const map = L.map('map').setView([53.5,-2.5],6);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',maxZoom:18}}).addTo(map);
map.createPane('stations');
map.getPane('stations').style.zIndex = 650;

const stationTickets = {{}};
TICKETS.forEach(t => t.stations.forEach(s => {{ if(!stationTickets[s]) stationTickets[s]=[]; stationTickets[s].push(t.id); }}));

const activeTickets = new Set();
const ticketLayers = {{}};
TICKETS.forEach(ticket => {{
  const color = COLORS[ticket.id];
  const group = L.layerGroup();
  ticket.stations.forEach(s => {{ const c=COORDS[s]; if(!c) return; L.circleMarker(c,{{radius:20,fillColor:color,fillOpacity:0.30,color:color,weight:1,opacity:0.45,interactive:false}}).addTo(group); }});
  ticketLayers[ticket.id] = group;
}});

Object.entries(stationTickets).forEach(([name,tids]) => {{
  const c = COORDS[name]; if(!c) return;
  const m = L.circleMarker(c,{{radius:5,fillColor:'#fff',fillOpacity:0.9,color:'#334',weight:1.5,pane:'stations'}});
  m.on('mouseover',()=>{{ m.setStyle({{radius:7}}); showPanel(name,tids); }});
  m.on('mouseout', ()=>{{ m.setStyle({{radius:5}}); hidePanel(); }});
  m.bindTooltip(name,{{permanent:false,direction:'top',offset:[0,-6]}});
  m.addTo(map);
}});

const panel=document.getElementById('hover-panel');
const panelName=document.getElementById('hover-name');
const panelList=document.getElementById('hover-tickets');
function showPanel(name,tids){{
  panelName.textContent=name; panelList.innerHTML='';
  tids.forEach(id=>{{
    const t=TICKETS.find(x=>x.id===id); if(!t) return;
    const price = priceSummary(t);
    const row=document.createElement('div'); row.className='hover-ticket';
    row.innerHTML=`<div class="hover-dot" style="background:${{COLORS[id]}}"></div><span class="hover-ticket-name">${{t.name}}</span>${{price!=null?`<span class="hover-ticket-price">${{price}}</span>`:''}}`;
    panelList.appendChild(row);
  }});
  if(!tids.length) panelList.innerHTML='<span style="color:#506070;font-size:10.5px;font-style:italic">No tickets</span>';
  panel.style.display='block';
}}
function hidePanel(){{ panel.style.display='none'; }}

const listEl=document.getElementById('ticket-list');
TICKETS.forEach(ticket=>{{
  const color=COLORS[ticket.id];
  const price = priceSummary(ticket);
  const apStr = price != null ? ` &middot; ${{price}}` : '';
  const el=document.createElement('div'); el.className='ticket-item'; el.dataset.id=ticket.id;
  el.innerHTML=`<div class="ticket-swatch" style="background:${{color}}"></div><div class="ticket-info"><div class="ticket-name">${{ticket.name}}</div><div class="ticket-meta">${{ticket.operator||'National Rail'}} &middot; ${{coverageLabel(ticket)}}${{apStr}}</div></div><a class="ticket-link" href="${{ticket.url}}" target="_blank" rel="noopener" title="Open on National Rail website" onclick="event.stopPropagation()"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg></a>${{buildPriceTooltip(ticket)}}`;
  el.addEventListener('click',()=>{{
    if(activeTickets.has(ticket.id)){{ activeTickets.delete(ticket.id); map.removeLayer(ticketLayers[ticket.id]); el.classList.remove('active'); el.style.borderLeftColor='transparent'; }}
    else {{ activeTickets.add(ticket.id); ticketLayers[ticket.id].addTo(map); el.classList.add('active'); el.style.borderLeftColor=color; }}
  }});
  listEl.appendChild(el);
}});

function selectAll(){{ TICKETS.forEach(t=>{{ const el=listEl.querySelector(`[data-id="${{t.id}}"]`); if(!activeTickets.has(t.id)){{ activeTickets.add(t.id); ticketLayers[t.id].addTo(map); el.classList.add('active'); el.style.borderLeftColor=COLORS[t.id]; }} }}); }}
function clearAll(){{ TICKETS.forEach(t=>{{ const el=listEl.querySelector(`[data-id="${{t.id}}"]`); activeTickets.delete(t.id); map.removeLayer(ticketLayers[t.id]); el.classList.remove('active'); el.style.borderLeftColor='transparent'; }}); }}
</script>
</body>
</html>"""

    with open(OUTPUT, "w") as f:
        f.write(html)
    print(f"Written {OUTPUT} ({len(tickets)} tickets, {len(used_coords)} stations)")


if __name__ == "__main__":
    main()

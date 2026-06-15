// Earliest-arrival reachability over the exported timetable dataset
// (data/timetable_export.json.gz), restricted to a single ticket's covered
// stations. Direct port of reachability.py -- see that file for the
// algorithm-level comments.

const END_OF_DAY = 24 * 60; // minutes
const STP_CANCEL = 3, STP_OVERLAY_O = 1, STP_OVERLAY_N = 2, STP_PERMANENT = 0;

const Reachability = (() => {

  async function loadDataset(url) {
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`Failed to fetch ${url}: ${resp.status}`);
    const stream = resp.body.pipeThrough(new DecompressionStream('gzip'));
    const text = await new Response(stream).text();
    const data = JSON.parse(text);

    const byUid = new Map();
    data.schedules.forEach((s, i) => {
      const uidIdx = s[0];
      let list = byUid.get(uidIdx);
      if (!list) byUid.set(uidIdx, (list = []));
      list.push(i);
    });
    data.byUid = byUid;

    const [ey, em, ed] = data.epoch.split('-').map(Number);
    data.epochUtc = Date.UTC(ey, em - 1, ed);

    return data;
  }

  // "YYYY-MM-DD" -> { dayNumber, weekday } (weekday: 0=Mon .. 6=Sun, matching
  // Python's date.weekday() and the dataset's daysRunMask bit order)
  function dateInfo(dataset, dateStr) {
    const [y, m, d] = dateStr.split('-').map(Number);
    const utc = Date.UTC(y, m - 1, d);
    const dayNumber = Math.round((utc - dataset.epochUtc) / 86400000);
    const jsDay = new Date(utc).getUTCDay(); // 0=Sun .. 6=Sat
    const weekday = (jsDay + 6) % 7; // 0=Mon .. 6=Sun
    return { dayNumber, weekday };
  }

  // {crs: [(dep_time, arr_time, dest_crs, uid), ...]} restricted to
  // crsFilter (Set of crsIdx), schedules active on the given date, and
  // (if allowedTocs is non-null) operated by a TOC in that set.
  function buildGraph(dataset, crsFilter, allowedTocs, dayNumber, weekday) {
    const graph = new Map();
    const { schedules } = dataset;

    for (const variantIdxs of dataset.byUid.values()) {
      const active = [];
      for (const idx of variantIdxs) {
        const s = schedules[idx];
        const [, startDay, endDay, daysRunMask, stp] = s;
        if (dayNumber < startDay || dayNumber > endDay) continue;
        if (!(daysRunMask & (1 << weekday))) continue;
        active.push(s);
      }
      if (!active.length) continue;

      if (active.some(s => s[4] === STP_CANCEL)) continue;

      let chosen = active.find(s => s[4] === STP_OVERLAY_O || s[4] === STP_OVERLAY_N);
      if (!chosen) chosen = active.find(s => s[4] === STP_PERMANENT);
      if (!chosen) continue;

      const [uidIdx, , , , , tocIdx, stops] = chosen;
      if (allowedTocs && !allowedTocs.has(tocIdx)) continue;

      // Find consecutive pairs of crsFilter stops, keeping the full
      // (universal-set) sequence of stops between them so the route can be
      // drawn through intermediate stations the train passes through.
      let prevIdx = -1;
      for (let i = 0; i < stops.length; i++) {
        const [crsIdx] = stops[i];
        if (!crsFilter.has(crsIdx)) continue;
        if (prevIdx >= 0) {
          const [crsA, , depA] = stops[prevIdx];
          const [crsB, arrB] = stops[i];
          if (depA !== -1 && arrB !== -1 && crsA !== crsB) {
            const path = stops.slice(prevIdx, i + 1).map(([c]) => c);
            let edges = graph.get(crsA);
            if (!edges) graph.set(crsA, (edges = []));
            edges.push([depA, arrB, crsB, uidIdx, path]);
          }
        }
        prevIdx = i;
      }
    }

    for (const edges of graph.values()) edges.sort((a, b) => a[0] - b[0]);
    return graph;
  }

  // Min-heap of [priority..., payload] arrays, compared lexicographically.
  class PriorityQueue {
    constructor() { this.items = []; }
    get length() { return this.items.length; }
    push(item) {
      const a = this.items;
      a.push(item);
      let i = a.length - 1;
      while (i > 0) {
        const p = (i - 1) >> 1;
        if (cmp(a[p], a[i]) <= 0) break;
        [a[p], a[i]] = [a[i], a[p]];
        i = p;
      }
    }
    pop() {
      const a = this.items;
      const top = a[0];
      const last = a.pop();
      if (a.length) {
        a[0] = last;
        let i = 0;
        while (true) {
          let smallest = i, l = 2 * i + 1, r = l + 1;
          if (l < a.length && cmp(a[l], a[smallest]) < 0) smallest = l;
          if (r < a.length && cmp(a[r], a[smallest]) < 0) smallest = r;
          if (smallest === i) break;
          [a[i], a[smallest]] = [a[smallest], a[i]];
          i = smallest;
        }
      }
      return top;
    }
  }
  function cmp(a, b) {
    for (let i = 0; i < a.length - 1; i++) {
      if (a[i] !== b[i]) return a[i] - b[i];
    }
    return 0;
  }

  function collapseLegs(edges, crsToName, crsCodes) {
    // edges: [[fromCrsIdx, depTime, toCrsIdx, arrTime, uidIdx, path], ...] in travel order
    const legs = [];
    for (const [fromIdx, dep, toIdx, arr, uid, path] of edges) {
      const last = legs[legs.length - 1];
      if (last && last._uid === uid) {
        last.to = toIdx;
        last.arr = arr;
        last._path.push(...path.slice(1));
      } else {
        legs.push({ from: fromIdx, dep, to: toIdx, arr, _uid: uid, _path: path.slice() });
      }
    }
    for (const leg of legs) {
      leg.path = leg._path.map(crsIdx => crsCodes[crsIdx]);
      leg.from = crsToName.get(leg.from) ?? leg.from;
      leg.to = crsToName.get(leg.to) ?? leg.to;
      delete leg._uid;
      delete leg._path;
    }
    return legs;
  }

  // State keys combine a station's crsIdx with a bitmask of which
  // "restricted pair" stations have been visited so far on this path, so
  // that routes which pass through both members of a not-valid-together
  // pair (see `restrictedPairs` in reachable()) can be excluded.
  const MASK_SPACE = 1 << 16; // supports up to 16 restricted stations per ticket
  function stateKey(crs, mask) { return crs * MASK_SPACE + mask; }
  function stateCrs(key) { return Math.floor(key / MASK_SPACE); }
  function stateMask(key) { return key % MASK_SPACE; }

  function reconstructItinerary(prevEdge, crsToName, crsCodes, startKey, destKey) {
    const edges = [];
    let node = destKey;
    while (node !== startKey) {
      const [fromCrs, dep, arr, uid, path, fromMask] = prevEdge.get(node);
      edges.push([fromCrs, dep, stateCrs(node), arr, uid, path]);
      node = stateKey(fromCrs, fromMask);
    }
    edges.reverse();
    return collapseLegs(edges, crsToName, crsCodes);
  }

  function reconstructReturnItinerary(prevEdgeBack, crsToName, crsCodes, startKey, destKey) {
    const edges = [];
    let node = destKey;
    while (node !== startKey) {
      const [nextCrs, dep, arr, uid, path, nextMask] = prevEdgeBack.get(node);
      edges.push([stateCrs(node), dep, nextCrs, arr, uid, path]);
      node = stateKey(nextCrs, nextMask);
    }
    return collapseLegs(edges, crsToName, crsCodes);
  }

  // Latest-departure-to-still-get-home search over the reversed graph.
  // Returns { g: Map<stateKey, latestDepartureMinute>, prevEdgeBack: Map<stateKey, [nextCrs, dep, arr, uid, path, nextMask]> }
  function latestReturn(graph, startCrs, startMask, maskOf, violates) {
    const reverseGraph = new Map();
    for (const [src, edges] of graph) {
      for (const [dep, arr, dest, uid, path] of edges) {
        let list = reverseGraph.get(dest);
        if (!list) reverseGraph.set(dest, (list = []));
        list.push([arr, dep, src, uid, path]);
      }
    }

    const startK = stateKey(startCrs, startMask);
    const g = new Map([[startK, END_OF_DAY]]);
    const prevEdgeBack = new Map();
    const pq = new PriorityQueue();
    pq.push([-END_OF_DAY, startCrs, startMask]);
    while (pq.length) {
      const [negG, crs, mask] = pq.pop();
      const gx = -negG;
      const k = stateKey(crs, mask);
      if (gx < (g.get(k) ?? -1)) continue;
      for (const [arr, dep, src, uid, path] of (reverseGraph.get(crs) || [])) {
        const newMask = mask | maskOf(src);
        if (violates(newMask)) continue;
        const dk = stateKey(src, newMask);
        if (arr <= gx && dep > (g.get(dk) ?? -1)) {
          g.set(dk, dep);
          prevEdgeBack.set(dk, [crs, dep, arr, uid, path, mask]);
          pq.push([-dep, src, newMask]);
        }
      }
    }
    return { g, prevEdgeBack };
  }

  // dataset: from loadDataset()
  // opts:
  //   crsFilter: Set<crsIdx>     -- stations on the ticket
  //   crsToName: Map<crsIdx,str> -- crsIdx -> station name, for crsFilter members
  //   allowedTocs: Set<tocIdx> | null
  //   startCrsIdx: crsIdx of the start station
  //   dateStr: "YYYY-MM-DD"
  //   startTimeStr: "HH:MM"
  //   mode: "one-way" | "return"
  //   restrictedPairs: [[crsIdxA, crsIdxB], ...] | undefined
  //     -- pairs of stations the ticket is not valid for travelling
  //     between (directly or via a route that passes through both)
  // returns Map<crsIdx, {arrival, changes, return_by?, itinerary, return_itinerary?}>
  function reachable(dataset, opts) {
    const { crsFilter, crsToName, allowedTocs, startCrsIdx, dateStr, startTimeStr, mode, restrictedPairs } = opts;
    const { dayNumber, weekday } = dateInfo(dataset, dateStr);
    const [h, m] = startTimeStr.split(':').map(Number);
    const startMinutes = h * 60 + m;

    const graph = buildGraph(dataset, crsFilter, allowedTocs, dayNumber, weekday);

    // Bit-per-station mask of "restricted pair" members visited on the
    // current path; a transition that would set both bits of any pair is
    // rejected (see stateKey/latestReturn above).
    const restrictedCrs = [...new Set((restrictedPairs || []).flat())];
    const bitOf = new Map(restrictedCrs.map((c, i) => [c, i]));
    const pairBits = (restrictedPairs || []).map(([a, b]) => [bitOf.get(a), bitOf.get(b)]);
    const maskOf = crs => {
      const bit = bitOf.get(crs);
      return bit === undefined ? 0 : (1 << bit);
    };
    const violates = mask => pairBits.some(([a, b]) => (mask & (1 << a)) && (mask & (1 << b)));

    const startMask = maskOf(startCrsIdx);
    const startKey = stateKey(startCrsIdx, startMask);

    const best = new Map([[startKey, [startMinutes, 0]]]); // stateKey -> [arrival, trains]
    const prevUid = new Map([[startKey, null]]);
    const prevEdge = new Map(); // stateKey -> [fromCrs, dep, arr, uid, path, fromMask]
    const pq = new PriorityQueue();
    pq.push([startMinutes, 0, startCrsIdx, startMask]);
    while (pq.length) {
      const [t, trains, crs, mask] = pq.pop();
      const k = stateKey(crs, mask);
      const b = best.get(k);
      if (t > b[0] || (t === b[0] && trains > b[1])) continue;
      for (const [dep, arr, dest, uid, path] of (graph.get(crs) || [])) {
        if (dep < t || arr > END_OF_DAY) continue;
        const newMask = mask | maskOf(dest);
        if (violates(newMask)) continue;
        const newTrains = uid === prevUid.get(k) ? trains : trains + 1;
        const dk = stateKey(dest, newMask);
        const cur = best.get(dk);
        if (!cur || arr < cur[0] || (arr === cur[0] && newTrains < cur[1])) {
          best.set(dk, [arr, newTrains]);
          prevUid.set(dk, uid);
          prevEdge.set(dk, [crs, dep, arr, uid, path, mask]);
          pq.push([arr, newTrains, dest, newMask]);
        }
      }
    }

    let returnG = null, returnPrevEdge = null;
    if (mode === 'return') {
      ({ g: returnG, prevEdgeBack: returnPrevEdge } = latestReturn(graph, startCrsIdx, startMask, maskOf, violates));
    }

    // Group reached states by station, ignoring the start station.
    const byStation = new Map(); // crs -> [[mask, [arrival, trains]], ...]
    for (const [k, v] of best) {
      const crs = stateCrs(k);
      if (crs === startCrsIdx) continue;
      let list = byStation.get(crs);
      if (!list) byStation.set(crs, (list = []));
      list.push([stateMask(k), v]);
    }

    let returnByStation = null;
    if (mode === 'return') {
      returnByStation = new Map();
      for (const [k, dep] of returnG) {
        const crs = stateCrs(k);
        let list = returnByStation.get(crs);
        if (!list) returnByStation.set(crs, (list = []));
        list.push([stateMask(k), dep]);
      }
    }

    const result = new Map();
    for (const [crs, states] of byStation) {
      const name = crsToName.get(crs);
      if (!name) continue;

      if (mode === 'return') {
        // Find the best valid (outbound mask, return mask) combo: visiting
        // both members of a restricted pair across the whole round trip
        // (outbound + return) is disallowed, even if neither leg alone
        // would violate it.
        const rstates = returnByStation.get(crs) || [];
        let chosen = null;
        for (const [m1, [arrival, trains]] of states) {
          for (const [m2, returnBy] of rstates) {
            if (violates(m1 | m2)) continue;
            if (arrival > returnBy) continue;
            if (!chosen || arrival < chosen.arrival || (arrival === chosen.arrival && trains < chosen.trains)) {
              chosen = { arrival, trains, returnBy, m1, m2 };
            }
          }
        }
        if (!chosen) continue;
        const entry = { arrival: chosen.arrival, changes: Math.max(chosen.trains - 1, 0), return_by: chosen.returnBy };
        entry.return_itinerary = reconstructReturnItinerary(returnPrevEdge, crsToName, dataset.crs, startKey, stateKey(crs, chosen.m2));
        entry.itinerary = reconstructItinerary(prevEdge, crsToName, dataset.crs, startKey, stateKey(crs, chosen.m1));
        result.set(name, entry);
      } else {
        let chosen = null;
        for (const [mask, [arrival, trains]] of states) {
          if (!chosen || arrival < chosen.arrival || (arrival === chosen.arrival && trains < chosen.trains)) {
            chosen = { arrival, trains, mask };
          }
        }
        const entry = { arrival: chosen.arrival, changes: Math.max(chosen.trains - 1, 0) };
        entry.itinerary = reconstructItinerary(prevEdge, crsToName, dataset.crs, startKey, stateKey(crs, chosen.mask));
        result.set(name, entry);
      }
    }
    return result;
  }

  return { loadDataset, reachable };
})();

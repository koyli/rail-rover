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

  function reconstructItinerary(prevEdge, crsToName, crsCodes, startCrs, destCrs) {
    const edges = [];
    let node = destCrs;
    while (node !== startCrs) {
      const [fromCrs, dep, arr, uid, path] = prevEdge.get(node);
      edges.push([fromCrs, dep, node, arr, uid, path]);
      node = fromCrs;
    }
    edges.reverse();
    return collapseLegs(edges, crsToName, crsCodes);
  }

  function reconstructReturnItinerary(prevEdgeBack, crsToName, crsCodes, startCrs, destCrs) {
    const edges = [];
    let node = destCrs;
    while (node !== startCrs) {
      const [nextCrs, dep, arr, uid, path] = prevEdgeBack.get(node);
      edges.push([node, dep, nextCrs, arr, uid, path]);
      node = nextCrs;
    }
    return collapseLegs(edges, crsToName, crsCodes);
  }

  // Latest-departure-to-still-get-home search over the reversed graph.
  // Returns { g: Map<crsIdx, latestDepartureMinute>, prevEdgeBack: Map<crsIdx, [nextCrs, dep, arr, uid]> }
  function latestReturn(graph, startCrs) {
    const reverseGraph = new Map();
    for (const [src, edges] of graph) {
      for (const [dep, arr, dest, uid, path] of edges) {
        let list = reverseGraph.get(dest);
        if (!list) reverseGraph.set(dest, (list = []));
        list.push([arr, dep, src, uid, path]);
      }
    }

    const g = new Map([[startCrs, END_OF_DAY]]);
    const prevEdgeBack = new Map();
    const pq = new PriorityQueue();
    pq.push([-END_OF_DAY, startCrs]);
    while (pq.length) {
      const [negG, crs] = pq.pop();
      const gx = -negG;
      if (gx < (g.get(crs) ?? -1)) continue;
      for (const [arr, dep, src, uid, path] of (reverseGraph.get(crs) || [])) {
        if (arr <= gx && dep > (g.get(src) ?? -1)) {
          g.set(src, dep);
          prevEdgeBack.set(src, [crs, dep, arr, uid, path]);
          pq.push([-dep, src]);
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
  // returns Map<crsIdx, {arrival, changes, return_by?, itinerary, return_itinerary?}>
  function reachable(dataset, opts) {
    const { crsFilter, crsToName, allowedTocs, startCrsIdx, dateStr, startTimeStr, mode } = opts;
    const { dayNumber, weekday } = dateInfo(dataset, dateStr);
    const [h, m] = startTimeStr.split(':').map(Number);
    const startMinutes = h * 60 + m;

    const graph = buildGraph(dataset, crsFilter, allowedTocs, dayNumber, weekday);

    const best = new Map([[startCrsIdx, [startMinutes, 0]]]); // crs -> [arrival, trains]
    const prevUid = new Map([[startCrsIdx, null]]);
    const prevEdge = new Map(); // crs -> [fromCrs, dep, arr, uid, path]
    const pq = new PriorityQueue();
    pq.push([startMinutes, 0, startCrsIdx]);
    while (pq.length) {
      const [t, trains, crs] = pq.pop();
      const b = best.get(crs);
      if (t > b[0] || (t === b[0] && trains > b[1])) continue;
      for (const [dep, arr, dest, uid, path] of (graph.get(crs) || [])) {
        if (dep < t || arr > END_OF_DAY) continue;
        const newTrains = uid === prevUid.get(crs) ? trains : trains + 1;
        const cur = best.get(dest);
        if (!cur || arr < cur[0] || (arr === cur[0] && newTrains < cur[1])) {
          best.set(dest, [arr, newTrains]);
          prevUid.set(dest, uid);
          prevEdge.set(dest, [crs, dep, arr, uid, path]);
          pq.push([arr, newTrains, dest]);
        }
      }
    }

    let returnBy = null, returnPrevEdge = null;
    if (mode === 'return') {
      ({ g: returnBy, prevEdgeBack: returnPrevEdge } = latestReturn(graph, startCrsIdx));
    }

    const result = new Map();
    for (const [crs, [arrival, trains]] of best) {
      if (crs === startCrsIdx) continue;
      if (mode === 'return') {
        const deadline = returnBy.get(crs);
        if (deadline === undefined || arrival > deadline) continue;
      }
      const name = crsToName.get(crs);
      if (!name) continue;
      const entry = { arrival, changes: Math.max(trains - 1, 0) };
      if (mode === 'return') {
        entry.return_by = returnBy.get(crs);
        entry.return_itinerary = reconstructReturnItinerary(returnPrevEdge, crsToName, dataset.crs, startCrsIdx, crs);
      }
      entry.itinerary = reconstructItinerary(prevEdge, crsToName, dataset.crs, startCrsIdx, crs);
      result.set(name, entry);
    }
    return result;
  }

  return { loadDataset, reachable };
})();

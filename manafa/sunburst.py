"""Animated sunburst of an app's call graph, coloured by energy.

Each ring is one call level, so a method that calls x and y contains x and y.
A sector is one call path: all the invocations of a method that have the same
callers. Two more views have 3 levels (package, class and method): one for all
the code, and one for the app only, with the calls of each method below it. The page also
has a search box, buttons for the number of levels and a ranked list of the
methods that consume the most energy in their own code.
A node's value is its inclusive energy, which means a caller always covers at
least the sum of its callees, and the colour scale follows that value: the
darker the sector, the more energy the call and everything below it consumed.

The call graph comes from the AM trace CSV (name, ts, dur, depth) recorded with
--trace-methods am. The energy comes from the power rails that the session
attributes to methods (the CPU rails): a frame gets the energy that these rails
consumed while it was running. Sessions without that time series fall back to
the session total, attributed in proportion to time on the stack.
"""
from __future__ import absolute_import

import bisect
import csv
import json
import math
import os

from manafa.utils.Logger import log, LogSeverity

DEFAULT_FRAMES = 40
DEFAULT_MAX_DEPTH = 10
# one hue, light -> dark as energy grows (log scale)
COLORSCALE = [[0, "#cde2fb"], [0.25, "#86b6ef"], [0.5, "#3987e5"], [0.75, "#1c5cab"], [1, "#0d366b"]]
# hot spots have a second hue, with the same light -> dark rule
HOT_COLORSCALE = [[0, "#fbd9c8"], [0.5, "#eb6834"], [1, "#8f3410"]]
HOT_COLOR = "#eb6834"
# a sector is a hot spot if it has more than HOT_TOTAL_PCT of the energy of the diagram, or more
# than HOT_RING_PCT of the energy of its ring, and if it also ran for more than HOT_MIN_MS
HOT_TOTAL_PCT = 10.0
HOT_RING_PCT = 20.0
HOT_MIN_MS = 10.0
BAR_COLOR = "#2a78d6"
SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
MIN_SHARE = 0.0005  # a call path with a smaller part of the attributed energy is not drawn
TOP_METHODS = 15

# search box, link from the bars to the call graph, and colours for a dark browser theme
PAGE_SCRIPT = """
var gd = document.getElementById('sunburst');
var dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
var surface = dark ? '#1a1a19' : '#fcfcfb';
var ink = dark ? '#f0efec' : '#0b0b0b';
document.body.style.background = surface;
document.body.style.margin = '0';
if (dark) {
    Plotly.relayout(gd, {paper_bgcolor: surface, plot_bgcolor: surface, 'font.color': ink,
        'updatemenus[0].font.color': '#0b0b0b', 'updatemenus[1].font.color': '#0b0b0b',
        'updatemenus[2].font.color': '#0b0b0b', 'updatemenus[2].bgcolor': '#e8e7e2',
        'updatemenus[0].bgcolor': '#e8e7e2', 'updatemenus[1].bgcolor': '#e8e7e2'});
    Plotly.restyle(gd, {'marker.line.color': surface}, [0, 2, 3]);
}
var row = document.createElement('div');
row.style.cssText = 'font: 14px system-ui, sans-serif; padding: 10px 12px 0; color: ' + ink;
var input = document.createElement('input');
input.type = 'search';
input.placeholder = 'Find a method or a package';
input.setAttribute('aria-label', 'Find a method or a package in the call graph');
input.style.cssText = 'font: inherit; width: 340px; padding: 6px 8px; border: 1px solid #8a8985; ' +
    'border-radius: 6px; background: ' + surface + '; color: ' + ink;
var info = document.createElement('span');
info.style.marginLeft = '12px';
row.appendChild(input);
row.appendChild(info);
gd.parentNode.insertBefore(row, gd);
function highlight() {
    var q = input.value.trim().toLowerCase();
    var found = 0, joules = 0;
    [0, 2, 3].forEach(function (trace) {  // the call graph and the two package views
        var paths = gd.data[trace].customdata;
        var widths = [], lines = [];
        for (var i = 0; i < paths.length; i++) {
            var match = q !== '' && i > 0 && paths[i][0].toLowerCase().indexOf(q) >= 0;
            if (match && trace === 0) { found++; joules += paths[i][1]; }
            widths.push(match ? 3 : 0.5);
            lines.push(match ? ink : surface);
        }
        Plotly.restyle(gd, {'marker.line.width': [widths], 'marker.line.color': [lines]}, [trace]);
    });
    info.textContent = q === '' ? '' : found + ' call paths found, ' + joules.toPrecision(3) +
        ' J in their own code. They have a thick outline. Select "all levels" to see the deep ones';
}
var timer = null;
input.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(highlight, 200); });
gd.on('plotly_click', function (ev) {
    var point = ev.points[0];
    if (point.curveNumber === 1) { input.value = point.customdata; highlight(); }
});
"""

# Frames that sit on the stack without running. Their wall-clock time is real
# but it is idle waiting, not work the app performed, so it earns no energy.
BLOCKING_METHODS = (
    "MessageQueue.nativePollOnce",
    "Object.wait",
    "Thread.sleep",
    "LockSupport.park",
    "Unsafe.park",
    "VMRuntime.runHeapTasks",
)
# Packages to start the graph at when the trace has no method of the app.
DEFAULT_FOCUS = ("android.app", "androidx")


def load_calls(csv_path):
    """Reads the AM trace CSV into invocation records ordered by start time.

    The file holds one section per trace capture, each introduced by its own
    header line and carrying an independent call stack, so every row is tagged
    with the section it came from.
    """
    calls = []
    section = -1
    with open(csv_path) as f:
        for row in csv.reader(f):
            if not row:
                continue
            if row[0] == "name":  # a new capture starts here
                section += 1
                continue
            calls.append({
                "name": row[0].split(":")[0],
                "ts": int(row[1]),
                "dur": int(row[2]),  # -1 while the frame never returned
                "depth": int(row[3]),
                "section": section,
            })
    if not calls:
        raise ValueError(f"no invocations found in {csv_path}")
    calls.sort(key=lambda c: (c["section"], c["ts"], c["depth"]))
    # a frame that never returned stays open until the end of its capture
    ends = {}
    for c in calls:
        ends[c["section"]] = max(ends.get(c["section"], 0), c["ts"] + max(c["dur"], 0))
    for c in calls:
        c["end"] = ends[c["section"]] if c["dur"] < 0 else c["ts"] + c["dur"]
    return calls, min(c["ts"] for c in calls), max(ends.values())


def build_tree(calls):
    """Links each invocation to its caller using stack depth within a capture."""
    parent = {}
    stack = []
    current = None
    for i, c in enumerate(calls):
        if c["section"] != current:  # each capture starts a fresh stack
            current, stack = c["section"], []
        # drop callers that already returned (concurrent threads share the depth
        # numbering, so containment is what separates them) and any frame that is
        # not strictly shallower than this one
        while stack and (calls[stack[-1]]["end"] <= c["ts"]
                         or calls[stack[-1]]["depth"] >= c["depth"]):
            stack.pop()
        parent[i] = stack[-1] if stack else None
        stack.append(i)
    children = {}
    for child, par in parent.items():
        if par is not None:
            children.setdefault(par, []).append(child)
    return parent, children


def energy_clock(series, t0, t1, total_energy):
    """Returns a function that gives the cumulative energy (J) at a trace time.

    series is [[ts, joules], ...] on the clock of the call trace. Without it,
    the session total is spread evenly over [t0, t1].
    """
    if not series:
        return lambda t: total_energy * (min(max(t, t0), t1) - t0) / ((t1 - t0) or 1)
    times = [s[0] for s in series]
    joules = [s[1] for s in series]

    def at(t):
        if t <= times[0]:
            return joules[0]
        if t >= times[-1]:
            return joules[-1]
        i = bisect.bisect_right(times, t)
        return joules[i - 1] + (joules[i] - joules[i - 1]) * (t - times[i - 1]) / (times[i] - times[i - 1])
    return at


def ramp_color(scale, x):
    """Colour at position x (0 to 1) of a colour scale [[position, '#rrggbb'], ...]."""
    for (x0, c0), (x1, c1) in zip(scale, scale[1:]):
        if x <= x1:
            k = (x - x0) / (x1 - x0)
            return "#" + "".join("%02x" % round(int(c0[j:j + 2], 16) + k * (int(c1[j:j + 2], 16) - int(c0[j:j + 2], 16)))
                                 for j in (1, 3, 5))
    return scale[-1][1]


def hot_sectors(ids, parents, values, times_ms, total_pct, ring_pct, min_ms):
    """Tells which sectors of a sunburst are hot spots. The first sector is the root.

    A sector is a hot spot if it has more than total_pct of the energy of the root, or more
    than ring_pct of the energy of its ring, and if it also ran for more than min_ms.
    """
    depth = {ids[0]: 0}
    ring_total = {}
    for k in range(1, len(ids)):  # a parent is always before its children
        depth[ids[k]] = depth[parents[k]] + 1
        ring_total[depth[ids[k]]] = ring_total.get(depth[ids[k]], 0.0) + values[k]
    hot = [False]
    for k in range(1, len(ids)):
        large = values[k] > total_pct / 100 * values[0] or values[k] > ring_pct / 100 * ring_total[depth[ids[k]]]
        hot.append(large and times_ms[k] > min_ms)
    return hot


def is_blocked(call):
    """True when the frame was waiting rather than running.

    A frame that never returned (dur -1) was still parked when the trace ended,
    which is what daemon threads and idle loopers do for a whole session.
    """
    return call["dur"] < 0 or any(b in call["name"] for b in BLOCKING_METHODS)


def focus_subtrees(calls, parent, prefixes):
    """Picks the topmost frames matching prefixes, and everything under them.

    Re-rooting this way keeps the framework work an app frame triggers (that
    energy is the app's doing) while dropping the process scaffolding above it,
    such as ZygoteInit -> RuntimeInit -> Method.invoke -> ActivityThread.main.
    """
    if not prefixes:
        return None
    def matches(i):
        return calls[i]["name"].startswith(tuple(prefixes))
    entry = set()
    for i in range(len(calls)):
        if not matches(i):
            continue
        p, outer = parent[i], False
        while p is not None:
            if matches(p):
                outer = True
                break
            p = parent[p]
        if not outer:
            entry.add(i)
    keep = set()
    for i in range(len(calls)):
        node = i
        while node is not None:
            if node in entry:
                keep.add(i)
                break
            node = parent[node]
    return keep, entry


def method_own_energy(session, include_blocked=False):
    """Energy that each method consumed in its own code (not in the methods that it calls).

    Uses the same rules as the diagram: a frame that only waits gets no energy, and frames
    of different threads that run at the same time share the energy.

    Args:
        session: results dict with 'energy' and 'method_invocations'.
    Returns:
        list of {'method', 'calls', 'joules', 'seconds'}, the highest energy first.
        'seconds' is the time of the calls, with their callees.
    """
    invocations = session.get("method_invocations")
    if not invocations or not invocations.get("trace_file"):
        raise ValueError("session has no method trace; profile with -a <package> --trace-methods am")
    energy = session.get("energy") or {}
    if not energy.get("total"):
        raise ValueError("session has no energy total")
    if not os.path.exists(invocations["trace_file"]):
        raise ValueError(f"call trace {invocations['trace_file']} is gone (each run wipes manafa/results)")
    calls, t0, t1 = load_calls(invocations["trace_file"])
    parent, children = build_tree(calls)
    energy_at = energy_clock(energy.get("method_energy_series"), t0, t1, energy["total"])

    def consumed(call):
        return max(0.0, energy_at(call["end"]) - energy_at(call["ts"]))
    own = {i: max(0.0, consumed(calls[i]) - sum(consumed(calls[k]) for k in children.get(i, [])))
           for i in parent if include_blocked or not is_blocked(calls[i])}
    span = sum(own.values())
    if not span:
        raise ValueError("the call trace and the energy samples do not overlap in time")
    share = min(1.0, (energy_at(t1) - energy_at(t0)) / span)
    methods = {}
    for i in parent:
        entry = methods.setdefault(calls[i]["name"], {'method': calls[i]["name"], 'calls': 0, 'joules': 0.0, 'seconds': 0.0})
        entry['calls'] += 1
        entry['joules'] += own.get(i, 0.0) * share
        entry['seconds'] += (calls[i]["end"] - calls[i]["ts"]) * 1e-9
    return sorted(methods.values(), key=lambda entry: entry['joules'], reverse=True)


def build_sunburst(session, out_file, max_depth=DEFAULT_MAX_DEPTH,
                   frames=DEFAULT_FRAMES, title=None, focus=None,
                   include_blocked=False, package=None, hot_total_pct=HOT_TOTAL_PCT,
                   hot_ring_pct=HOT_RING_PCT, hot_min_ms=HOT_MIN_MS):
    """Writes the animated sunburst for a profiling session to out_file.

    Args:
        session: results dict (or parsed emanafa_*.json) with 'energy' and
            'method_invocations'.
        out_file: path of the html file to write.
        max_depth: deepest call level to include.
        frames: number of animation steps.
        title: chart title. Defaults to the app name and energy total.
        focus: package prefixes to re-root on. Defaults to the app package (or to
            the app package plus DEFAULT_FOCUS, if the trace has no method of the
            app); pass ['all'] to keep the whole process tree.
        include_blocked: charge energy to frames that were only waiting.
        package: package prefix of the "app only" view. Defaults to the app package.
        hot_total_pct, hot_ring_pct, hot_min_ms: limits of a hot spot. See hot_sectors.
    Returns:
        path of the file written.
    """
    import plotly.graph_objects as go  # optional dependency, only needed here

    invocations = session.get("method_invocations")
    if not invocations or not invocations.get("trace_file"):
        raise ValueError("session has no method trace; profile with "
                         "-a <package> --trace-methods am")
    energy = session.get("energy") or {}
    total_energy = energy.get("total")
    if not total_energy:
        raise ValueError("session has no energy total to colour the call graph with")
    csv_path = invocations["trace_file"]
    if not os.path.exists(csv_path):
        raise ValueError(f"call trace {csv_path} is gone (each run wipes manafa/results)")

    calls, t0, t1 = load_calls(csv_path)
    parent, children = build_tree(calls)

    if focus is None:
        # the methods of the app and all that they call. if the trace has no method
        # of the app, the android packages are used
        focus = [p for p in (session.get("app"),) if p]
        if not focus or not focus_subtrees(calls, parent, focus)[0]:
            focus = focus + list(DEFAULT_FOCUS)
    elif list(focus) == ["all"]:
        focus = []
    kept = focus_subtrees(calls, parent, focus)
    if kept is not None:
        keep, entry = kept
        if not keep:
            raise ValueError(f"no calls matched {', '.join(focus)}; "
                             "widen --focus or pass --focus all")
        for i in list(parent):
            if i not in keep:
                parent.pop(i)
            elif i in entry:
                parent[i] = None  # entry points hang off the session root
        children = {}
        for child, par in parent.items():
            if par is not None:
                children.setdefault(par, []).append(child)
    # depth is counted from the entry point, not from process start, so the cap
    # trims the deep tail of app work rather than the framework frames above it
    rel = {}
    for i in sorted(parent, key=lambda i: calls[i]["depth"]):
        rel[i] = 0 if parent[i] is None else rel[parent[i]] + 1
    for i in [i for i, d in rel.items() if d > max_depth]:
        parent.pop(i)
    children = {}
    for child, par in parent.items():
        if par is not None and par in parent:
            children.setdefault(par, []).append(child)
    index = sorted(parent)

    # A frame gets the energy that was consumed while it ran. A caller's share is
    # the sum of its own share and its callees', so a caller always covers its
    # callees. Blocked frames contribute nothing, which keeps parked daemons and
    # idle loopers from soaking up the session.
    energy_at = energy_clock(energy.get("method_energy_series"), t0, t1, total_energy)

    def consumed_at(call, t):
        """Energy consumed while the invocation was on the stack, as of t."""
        return max(0.0, energy_at(min(t, call["end"])) - energy_at(call["ts"]))

    active = {i: (include_blocked or not is_blocked(calls[i])) for i in index}

    def own_at(i, t):
        """Energy consumed while the invocation ran outside its callees, as of t."""
        if not active[i]:
            return 0.0
        return max(0.0, consumed_at(calls[i], t) - sum(consumed_at(calls[k], t)
                                                       for k in children.get(i, [])))

    span = sum(own_at(i, t1) for i in index)
    if not span:
        raise ValueError("the call trace and the energy samples do not overlap in time")
    # frames of different threads run at the same time. scale down so that the
    # same energy is not given to more than one of them.
    share = min(1.0, (energy_at(t1) - energy_at(t0)) / span)

    def short(name):
        parts = name.split(".")
        return ".".join(parts[-2:]) if len(parts) > 1 else name

    def clip(label, size=34):
        return label if len(label) <= size else label[:size - 1] + "…"

    # one node for each call path: all the invocations of a method that have the
    # same callers are added together. One node for each invocation gives a file
    # of hundreds of MB for a session of 20 s.
    nodes = {}  # (node of the caller, method name) -> node
    node_of = {}
    for i in index:  # a caller is always before its callees
        caller = None if parent[i] is None else node_of[parent[i]]
        node_of[i] = nodes.setdefault((caller, calls[i]["name"]), len(nodes))
    node_keys = list(nodes)
    calls_of = [0] * len(node_keys)
    for i in index:
        calls_of[node_of[i]] += 1

    def self_at(t):
        """Energy that each call path consumed in its own code, as of t."""
        selfs = [0.0] * len(node_keys)
        for i in index:
            selfs[node_of[i]] += own_at(i, t) * share
        return selfs

    def inclusive(selfs):
        """Energy of each call path with its callees, and the total."""
        vals = list(selfs)
        total = 0.0
        for n in range(len(node_keys) - 1, -1, -1):  # callees are after their caller
            caller = node_keys[n][0]
            if caller is None:
                total += vals[n]
            else:
                vals[caller] += vals[n]
        return vals, total

    selfs_end = self_at(t1)
    vals_end, attributed = inclusive(selfs_end)
    # call paths with almost no energy are not drawn. their energy stays in their caller
    shown = [n for n in range(len(node_keys)) if vals_end[n] >= MIN_SHARE * attributed]

    # time (ms) that each call path ran in its own code. a method that waits has no time
    times_self = [0.0] * len(node_keys)
    for i in index:
        if active[i]:
            own = calls[i]["end"] - calls[i]["ts"] - sum(calls[k]["end"] - calls[k]["ts"]
                                                         for k in children.get(i, []))
            times_self[node_of[i]] += max(0, own) / 1e6
    times_end, time_total = inclusive(times_self)

    final = [attributed] + [vals_end[n] for n in shown]
    scale = [math.log10(v) for v in final if v > 0]
    lo, hi = (min(scale), max(scale)) if scale else (0, 1)
    decades = list(range(math.ceil(lo), math.floor(hi) + 1)) or [round(hi)]

    def colors(vals, hot):
        """Blue for a usual sector and orange for a hot spot. Darker with more energy (log scale)."""
        return [ramp_color(HOT_COLORSCALE if is_hot else COLORSCALE,
                           0.0 if v <= 0 else min(1.0, max(0.0, (math.log10(v) - lo) / ((hi - lo) or 1))))
                for v, is_hot in zip(vals, hot)]

    def paint(vals, hot):
        """Colour of the sectors, and a text colour that can be read on each of them."""
        fills = colors(vals, hot)
        inks = ["#0b0b0b" if 0.299 * int(c[1:3], 16) + 0.587 * int(c[3:5], 16) + 0.114 * int(c[5:7], 16) > 150
                else "#ffffff" for c in fills]
        return dict(marker=dict(colors=fills, line=dict(color=SURFACE, width=0.5)), insidetextfont=dict(color=inks))

    def hot(ids, parents, values, times_ms):
        return hot_sectors(ids, parents, values, times_ms, hot_total_pct, hot_ring_pct, hot_min_ms)

    def details(names, own, counts, times_ms, values):
        """Hover data of each sector: name, own energy, calls, time, mean energy of a call."""
        return [[name, joules, count, ms, value / count if count else 0.0]
                for name, joules, count, ms, value in zip(names, own, counts, times_ms, values)]

    HOVER_END = ("%{customdata[2]} calls, mean %{customdata[4]:.3g} J for each call, %{customdata[3]:.4g} ms<br>"
                 "%{customdata[0]}<extra></extra>")

    call_ids = ["session"] + [str(n) for n in shown]
    call_parents = [""] + ["session" if node_keys[n][0] is None else str(node_keys[n][0]) for n in shown]
    call_times = [time_total] + [times_end[n] for n in shown]
    call_hot = hot(call_ids, call_parents, final, call_times)
    sunburst = go.Sunburst(
        ids=call_ids, labels=["session"] + [short(node_keys[n][1]) for n in shown], parents=call_parents,
        values=final, branchvalues="total", maxdepth=4, domain=dict(x=[0.06, 0.66], y=[0.13, 1]),
        customdata=details(["all traced methods"] + [node_keys[n][1] for n in shown],
                           [0.0] + [selfs_end[n] for n in shown],
                           [len(index)] + [calls_of[n] for n in shown], call_times, final),
        texttemplate="%{label}<br>%{percentRoot:.0%}", insidetextorientation="radial",
        hovertemplate="<b>%{value:.3g} J</b> with its callees (%{percentRoot:.1%} of the attributed energy)<br>"
                      "%{customdata[1]:.3g} J in its own code<br>" + HOVER_END,
        **paint(final, call_hot),
    )
    # the sectors have explicit colours, so this empty trace gives the colour bar
    colorbar = go.Scatter(
        x=[None], y=[None], mode="markers", hoverinfo="skip",
        marker=dict(color=[lo], colorscale=COLORSCALE, cmin=lo, cmax=hi, showscale=True,
                    colorbar=dict(title="energy", x=0, xanchor="left", y=0.6, len=0.5, thickness=12, outlinewidth=0,
                                  tickvals=decades, ticktext=[f"{10.0 ** d:g} J" for d in decades])))

    # second view, 3 levels: package, class, method. a sector is the energy that the
    # methods consumed in their own code
    def split(name):
        parts = name.split(".")
        return ".".join(parts[:-2]) or "(no package)", parts[-2] if len(parts) > 1 else "(no class)", parts[-1]

    groups = {}  # id of the sector -> (label, id of the parent, full name)
    group_of = []  # call path -> id of its method sector
    for _, name in node_keys:
        pkg, cls, method = split(name)
        groups.setdefault("p:" + pkg, (pkg, "session", pkg))
        groups.setdefault("c:" + pkg + "." + cls, (cls, "p:" + pkg, pkg + "." + cls))
        groups.setdefault("m:" + name, (method, "c:" + pkg + "." + cls, name))
        group_of.append("m:" + name)

    def group_values(selfs, counts=None):
        vals = dict.fromkeys(groups, 0.0)
        for n, joules in enumerate(selfs if counts is None else counts):
            key = group_of[n]
            while key != "session":
                vals[key] += joules
                key = groups[key][1]
        return vals

    group_end = group_values(selfs_end)
    group_calls = group_values(selfs_end, calls_of)
    group_shown = [g for g in groups if group_end[g] >= MIN_SHARE * attributed]

    def group_values_at(selfs):
        vals = group_values(selfs)
        return [sum(v for g, v in vals.items() if g.startswith("p:"))] + [vals[g] for g in group_shown]

    group_final = group_values_at(selfs_end)
    group_ids = ["session"] + group_shown
    group_parents = [""] + [groups[g][1] for g in group_shown]
    group_times = group_values_at(times_self)
    group_hot = hot(group_ids, group_parents, group_final, group_times)
    packages = go.Sunburst(
        ids=group_ids, labels=["session"] + [groups[g][0] for g in group_shown], parents=group_parents,
        values=group_final, branchvalues="total", domain=dict(x=[0.06, 0.66], y=[0.13, 1]), visible=False,
        customdata=details(["all traced methods"] + [groups[g][2] for g in group_shown], group_final,
                           [len(index)] + [int(group_calls[g]) for g in group_shown], group_times, group_final),
        texttemplate="%{label}<br>%{percentRoot:.0%}", insidetextorientation="radial",
        hovertemplate="<b>%{value:.3g} J</b> in its own code (%{percentRoot:.1%} of the attributed energy)<br>" +
                      HOVER_END,
        **paint(group_final, group_hot),
    )

    # third view: only the packages, classes and methods of the app (3 levels). below a
    # method are the calls that it makes to other code, with their energy. a call to
    # another method of the app is not below it: that method has its own sector
    prefix = package or session.get("app") or ""
    app_ids = {}  # key -> position in app_info
    app_info = []  # (label, position of the parent, full name)

    def app_node(key, label, above, full_name):
        if key not in app_ids:
            app_ids[key] = len(app_info)
            app_info.append((label, above, full_name))
        return app_ids[key]

    app_of = []  # call path -> position in app_info. None if it is not below a method of the app
    for caller, name in node_keys:
        if prefix and name.startswith(prefix):
            pkg, cls, method = split(name)
            above = app_node(("p", pkg), pkg, None, pkg)
            above = app_node(("c", pkg, cls), cls, above, pkg + "." + cls)
            app_of.append(app_node(("m", name), method, above, name))
        else:
            above = None if caller is None else app_of[caller]
            app_of.append(None if above is None else app_node((above, name), short(name), above, name))

    def app_values(selfs):
        """Own value and value with callees of each sector of the app view, and the total."""
        own = [0.0] * len(app_info)
        for n, joules in enumerate(selfs):
            if app_of[n] is not None:
                own[app_of[n]] += joules
        vals = list(own)
        total = 0.0
        for pos in range(len(app_info) - 1, -1, -1):  # a parent is always before its children
            above = app_info[pos][1]
            if above is None:
                total += vals[pos]
            else:
                vals[above] += vals[pos]
        return own, vals, total

    app_own, app_end, app_total = app_values(selfs_end)
    app_calls = app_values(calls_of)[1]
    app_shown = [pos for pos in range(len(app_info)) if app_end[pos] >= MIN_SHARE * attributed]

    def app_values_at(selfs):
        _, vals, total = app_values(selfs)
        return [total] + [vals[pos] for pos in app_shown]

    app_final = [app_total] + [app_end[pos] for pos in app_shown]
    app_view_ids = ["session"] + [f"a:{pos}" for pos in app_shown]
    app_parents = [""] + ["session" if app_info[pos][1] is None else f"a:{app_info[pos][1]}" for pos in app_shown]
    app_times = app_values_at(times_self)
    app_hot = hot(app_view_ids, app_parents, app_final, app_times)
    app_view = go.Sunburst(
        ids=app_view_ids, labels=[prefix or "app"] + [app_info[pos][0] for pos in app_shown], parents=app_parents,
        values=app_final, branchvalues="total", maxdepth=5, domain=dict(x=[0.06, 0.66], y=[0.13, 1]),
        visible=False,
        customdata=details(["all methods of " + (prefix or "the app")] + [app_info[pos][2] for pos in app_shown],
                           [0.0] + [app_own[pos] for pos in app_shown],
                           [len(index)] + [int(app_calls[pos]) for pos in app_shown], app_times, app_final),
        texttemplate="%{label}<br>%{percentRoot:.0%}", insidetextorientation="radial",
        hovertemplate="<b>%{value:.3g} J</b> with the code that it calls (%{percentRoot:.1%} of the app)<br>"
                      "%{customdata[1]:.3g} J in its own code<br>" + HOVER_END,
        **paint(app_final, app_hot),
    )

    # ranked bars: the methods that consume the most energy in their own code, in all call paths
    by_method = {}
    for n, (_, name) in enumerate(node_keys):
        by_method[name] = by_method.get(name, 0.0) + selfs_end[n]
    top = sorted(by_method.items(), key=lambda x: -x[1])[:TOP_METHODS]
    bars = go.Bar(
        x=[joules for _, joules in top], y=list(range(len(top))), orientation="h",
        customdata=[name for name, _ in top], marker=dict(color=BAR_COLOR),
        text=[f"{joules:.3g} J" for _, joules in top], textposition="outside", cliponaxis=False,
        hovertemplate="<b>%{x:.3g} J</b> in its own code<br>%{customdata}<br>"
                      "click to find it in the call graph<extra></extra>",
    )

    steps = [t0 + (t1 - t0) * (k + 1) / frames for k in range(frames)]
    # a step holds only what changes (values and colours), to keep the file small
    anim = []
    for t in steps:
        selfs = self_at(t)
        vals, total = inclusive(selfs)
        vals = [total] + [vals[n] for n in shown]
        group_vals = group_values_at(selfs)
        app_vals = app_values_at(selfs)
        anim.append(go.Frame(data=[go.Sunburst(values=vals, **paint(vals, call_hot)),
                                   go.Sunburst(values=group_vals, **paint(group_vals, group_hot)),
                                   go.Sunburst(values=app_vals, **paint(app_vals, app_hot))],
                             traces=[0, 2, 3], name=f"{(t - t0) / 1e9:.2f}"))

    fig = go.Figure(data=[sunburst, bars, packages, app_view, colorbar], frames=anim)
    fig.update_layout(
        title=dict(text=title or (f"Call graph energy of {session.get('app') or 'app'}"
                                  f"<br><sup>{attributed:.3f} J attributed to methods. "
                                  f"The session total is {total_energy:.3f} J. Click a sector to zoom in</sup>"),
                   x=0.01, xanchor="left", y=0.98, yanchor="top"),
        font=dict(family="system-ui, -apple-system, 'Segoe UI', sans-serif", color=TEXT),
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, showlegend=False,
        margin=dict(t=135, l=10, r=70, b=10),
        xaxis=dict(domain=[0.82, 1], visible=False),
        yaxis=dict(domain=[0.12, 0.9], autorange="reversed", tickvals=list(range(len(top))),
                   ticktext=[clip(short(name)) for name, _ in top], showgrid=False),
        annotations=[dict(text="<b>Methods with the most energy in their own code</b>", showarrow=False,
                          xref="paper", yref="paper", x=1, xanchor="right", y=0.94),
                     dict(text=f"<span style='color:{HOT_COLOR}'>■</span> <b>hot spot</b><br>"
                               f"more than {hot_total_pct:g} % of the total<br>or more than {hot_ring_pct:g} % "
                               f"of its ring,<br>and more than {hot_min_ms:g} ms",
                          showarrow=False, align="left", xref="paper", yref="paper",
                          x=0, xanchor="left", y=0.3, yanchor="top", font=dict(size=11))],
        updatemenus=[
            dict(type="buttons", direction="right", showactive=False, x=0, xanchor="left", y=0, yanchor="bottom",
                 buttons=[
                     dict(label="Play", method="animate",
                          args=[None, dict(frame=dict(duration=120, redraw=True),
                                           fromcurrent=True)]),
                     dict(label="Pause", method="animate",
                          args=[[None], dict(frame=dict(duration=0, redraw=False),
                                             mode="immediate")]),
                 ]),
            dict(type="buttons", direction="right", active=0, x=0.06, xanchor="left", y=1.02, yanchor="bottom",
                 buttons=[dict(label=label, method="restyle", args=[{"visible": visible}, [0, 2, 3]])
                          for label, visible in (("Call graph", [True, False, False]),
                                                 ("Packages: all code", [False, True, False]),
                                                 ("Packages: app only", [False, False, True]))]),
            dict(type="buttons", direction="right", showactive=False, x=0.66, xanchor="right", y=1.02, yanchor="bottom",
                 buttons=[dict(label=label, method="restyle", args=[{"maxdepth": levels}, [0, 3]])
                          for label, levels in (("3 levels", 3), ("4 levels", 4), ("6 levels", 6),
                                                ("all levels", -1))]),
        ],
        sliders=[dict(
            active=len(anim) - 1, x=0.12, len=0.54, y=0, yanchor="bottom", pad=dict(t=0, b=10),
            currentvalue=dict(prefix="energy until t = ", suffix=" s"),
            steps=[dict(label=f.name, method="animate",
                        args=[[f.name], dict(mode="immediate",
                                             frame=dict(duration=0, redraw=True))])
                   for f in anim])],
    )
    fig.write_html(out_file, div_id="sunburst", default_height="96vh", auto_play=False, post_script=PAGE_SCRIPT)
    log(f"Sunburst: {len(index)} invocations, {len(node_keys)} call paths ({len(shown)} drawn), "
        f"{attributed:.3f} J of {total_energy:.3f} J attributed "
        f"({100 * attributed / total_energy:.1f}%) -> {out_file}",
        log_sev=LogSeverity.INFO)
    return out_file


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json_file", help="emanafa_*.json from a run with --trace-methods am")
    ap.add_argument("-o", "--out", default="emanafa_sunburst.html")
    ap.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)
    ap.add_argument("--frames", type=int, default=DEFAULT_FRAMES)
    ap.add_argument("--focus", default=None,
                    help="comma-separated package prefixes to re-root on "
                         "(default: the app package; 'all' keeps everything)")
    ap.add_argument("--include-blocked", action="store_true",
                    help="charge energy to frames that were only waiting "
                         "(idle loopers, parked daemons)")
    ap.add_argument("--package", default=None,
                    help="package prefix of the 'Packages: app only' view (default: the app package)")
    ap.add_argument("--hot-total", type=float, default=HOT_TOTAL_PCT,
                    help="a sector with more than this percentage of the energy of the diagram is a hot spot")
    ap.add_argument("--hot-ring", type=float, default=HOT_RING_PCT,
                    help="a sector with more than this percentage of the energy of its ring is a hot spot")
    ap.add_argument("--hot-min-ms", type=float, default=HOT_MIN_MS,
                    help="a hot spot must also run for more than this time (ms)")
    args = ap.parse_args()
    with open(args.json_file) as f:
        build_sunburst(json.load(f), args.out, args.max_depth, args.frames,
                       focus=args.focus.split(",") if args.focus else None,
                       include_blocked=args.include_blocked, package=args.package,
                       hot_total_pct=args.hot_total, hot_ring_pct=args.hot_ring, hot_min_ms=args.hot_min_ms)


if __name__ == "__main__":
    main()

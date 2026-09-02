"""Animated sunburst of an app's call graph, coloured by energy.

Each ring is one call level, so a method that calls x and y contains x and y.
A node's value is its inclusive energy, which means a caller always covers at
least the sum of its callees, and the colour scale follows that value: the
darker the sector, the more energy the call and everything below it consumed.

The call graph comes from the AM trace CSV (name, ts, dur, depth) recorded with
--trace-methods am; the energy total comes from the profiling session itself.
Energy is attributed in proportion to time on the stack.
"""
from __future__ import absolute_import

import csv
import json
import math
import os

from manafa.utils.Logger import log, LogSeverity

DEFAULT_FRAMES = 40
DEFAULT_MAX_DEPTH = 10
COLORSCALE = "Blues"  # light -> dark as energy grows

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
# Packages worth looking at for app performance work. The app's own package is
# added on top of these.
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


def elapsed_at(call, t):
    """Time the invocation has spent running as of t."""
    return max(0.0, min(t, call["end"]) - call["ts"])


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


def build_sunburst(session, out_file, max_depth=DEFAULT_MAX_DEPTH,
                   frames=DEFAULT_FRAMES, title=None, focus=None,
                   include_blocked=False):
    """Writes the animated sunburst for a profiling session to out_file.

    Args:
        session: results dict (or parsed emanafa_*.json) with 'energy' and
            'method_invocations'.
        out_file: path of the html file to write.
        max_depth: deepest call level to include.
        frames: number of animation steps.
        title: chart title. Defaults to the app name and energy total.
        focus: package prefixes to re-root on. Defaults to the app package plus
            DEFAULT_FOCUS; pass ['all'] to keep the whole process tree.
        include_blocked: charge energy to frames that were only waiting.
    Returns:
        path of the file written.
    """
    import plotly.graph_objects as go  # optional dependency, only needed here

    invocations = session.get("method_invocations")
    if not invocations or not invocations.get("trace_file"):
        raise ValueError("session has no method trace; profile with "
                         "-a <package> --trace-methods am")
    total_energy = (session.get("energy") or {}).get("total")
    if not total_energy:
        raise ValueError("session has no energy total to colour the call graph with")
    csv_path = invocations["trace_file"]
    if not os.path.exists(csv_path):
        raise ValueError(f"call trace {csv_path} is gone (each run wipes manafa/results)")

    calls, t0, t1 = load_calls(csv_path)
    parent, children = build_tree(calls)

    if focus is None:
        focus = [p for p in (session.get("app"),) if p] + list(DEFAULT_FOCUS)
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
    roots = [i for i in index if parent[i] is None]

    # Energy follows time spent running. A caller's share is the sum of its own
    # running time and its callees', so a caller always covers its callees.
    # Blocked frames contribute nothing, which keeps parked daemons and idle
    # loopers from soaking up the session.
    span = sum(calls[i]["end"] - calls[i]["ts"] for i in roots) or 1
    joules_per_ns = total_energy / span
    active = {i: (include_blocked or not is_blocked(calls[i])) for i in index}

    def short(name):
        parts = name.split(".")
        return ".".join(parts[-2:]) if len(parts) > 1 else name

    ids = ["session"] + [str(i) for i in index]
    labels = ["session"] + [short(calls[i]["name"]) for i in index]
    parents = [""] + ["session" if parent[i] is None else str(parent[i])
                      for i in index]
    hover = ["session total"] + [calls[i]["name"] for i in index]
    order = sorted(index, key=lambda i: -calls[i]["depth"])

    def values_at(t):
        # self time is what the frame ran outside its callees
        vals = {}
        for i in order:
            own = elapsed_at(calls[i], t) - sum(elapsed_at(calls[k], t)
                                                for k in children.get(i, []))
            own = max(0.0, own) if active[i] else 0.0
            vals[i] = own * joules_per_ns + sum(vals[k] for k in children.get(i, []))
        return [sum(vals[i] for i in roots)] + [vals[i] for i in index]

    def colors(vals):
        return [math.log10(v) if v > 0 else None for v in vals]

    scale = [c for c in colors(values_at(t1)) if c is not None]
    lo, hi = (min(scale), max(scale)) if scale else (0, 1)

    def trace(vals):
        return go.Sunburst(
            ids=ids, labels=labels, parents=parents, values=vals,
            branchvalues="total", maxdepth=4, customdata=hover,
            hovertemplate="%{customdata}<br>%{value:.4g} J<extra></extra>",
            marker=dict(colors=colors(vals), colorscale=COLORSCALE, cmin=lo, cmax=hi,
                        colorbar=dict(title="log₁₀ J")),
        )

    steps = [t0 + (t1 - t0) * (k + 1) / frames for k in range(frames)]
    anim = [go.Frame(data=[trace(values_at(t))], name=f"{(t - t0) / 1e9:.2f}")
            for t in steps]

    fig = go.Figure(data=[trace(values_at(steps[-1]))], frames=anim)
    fig.update_layout(
        title=title or (f"Call graph energy — {session.get('app') or 'app'} — "
                        f"{values_at(t1)[0]:.3f} J attributed "
                        f"of {total_energy:.3f} J session total"),
        margin=dict(t=60, l=0, r=0, b=0),
        updatemenus=[dict(
            type="buttons", showactive=False, x=0.05, y=0.05,
            buttons=[
                dict(label="Play", method="animate",
                     args=[None, dict(frame=dict(duration=120, redraw=True),
                                      fromcurrent=True)]),
                dict(label="Pause", method="animate",
                     args=[[None], dict(frame=dict(duration=0, redraw=False),
                                        mode="immediate")]),
            ])],
        sliders=[dict(
            active=len(anim) - 1,
            currentvalue=dict(prefix="t = ", suffix=" s"),
            steps=[dict(label=f.name, method="animate",
                        args=[[f.name], dict(mode="immediate",
                                             frame=dict(duration=0, redraw=True))])
                   for f in anim])],
    )
    fig.write_html(out_file)
    attributed = values_at(t1)[0]
    log(f"Sunburst: {len(index)} invocations, {len(roots)} entry points, "
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
                         "(default: the app package plus "
                         f"{', '.join(DEFAULT_FOCUS)}; 'all' keeps everything)")
    ap.add_argument("--include-blocked", action="store_true",
                    help="charge energy to frames that were only waiting "
                         "(idle loopers, parked daemons)")
    args = ap.parse_args()
    with open(args.json_file) as f:
        build_sunburst(json.load(f), args.out, args.max_depth, args.frames,
                       focus=args.focus.split(",") if args.focus else None,
                       include_blocked=args.include_blocked)


if __name__ == "__main__":
    main()

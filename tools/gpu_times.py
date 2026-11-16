"""Where the GPU's time goes: a capture's draw log (captures/<id>_draws.tsv,
"gpu us" column), per pass, or two captures side by side (stock, then mod).

    python tools/gpu_times.py 352               # one log
    python tools/gpu_times.py 352 353           # stock (Ctrl+Shift+F9 off) vs mod
    python tools/gpu_times.py 352 353 --top 40  # more rows
    python tools/gpu_times.py 677,679,681 676,678,680  # several logs per side, averaged

One log often catches a fragment of a frame, and the clouds' work changes from
frame to frame: for a comparison, take several logs of each side in a frozen
view (the sim paused) and give them comma-separated, summed per frame. The cloud
renderer's own time per call is printed per side too (a log that caught only a
fragment of a frame may miss it), and a WEATHER warning when the add-on's METAR
stations faded in new clouds during or between the logs (weather.log): a fade
of 300 s changed the cloud pass by 0.36 ms between two logs of the same shaders
(16 Nov 2026). The add-on holds its fades while the sim is paused (any pause:
weather.log's "the sim is paused" and "the sim runs again" lines), so logs
taken in one pause see the same weather; before, they didn't hold. Swap the
sides back and forth (A B A B) to see drift.

Take the logs with Ctrl+Shift+F12 (the draw log alone): a capture's (F11) own
times include the replacements writing the capture.

Each draw or dispatch's time runs from its timestamp to the next on its
command list, so the list's copies, clears and barriers count to the entry
before them. Per frame: divided by the number of ps_deferredRendering draws
(one per rendered frame; the log spans two presents). Replaced passes are
marked * (a .cso in build/shaders with their stock crc). With "gpu at us" (each
entry's start on the GPU), also the GPU's timeline: busy time, span and the
longest idle gaps, where it waited (for the CPU to submit, or another queue).
"""

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAPTURES = ROOT / "build" / "logs" / "captures"
LOGS = ROOT / "build" / "logs"
CLOUD_PASS = "ch_volumetric_clouds_render"
CROSSFADE_S = 300  # an updated station's clouds and haze (cloud_stations.h)


def seconds(hms: str) -> int:
    h, m, s = map(int, hms.split(":"))
    return 3600 * h + 60 * m + s


def unwrapped(lines):
    """(seconds since the first line's midnight, line) for lines starting with HH:MM:SS, past midnight too."""
    day, last = 0, None
    for line in lines:
        if not re.match(r"\d\d:\d\d:\d\d ", line):
            continue
        t = seconds(line[:8])
        if last is not None and t < last - 3600:
            day += 86400
        last = t
        yield t + day, line


def weather_clock(lines):
    """Time (s) -> the add-on's weather clock, which stops while the sim is paused (the pause lines in
    `lines`, (time, line) pairs as unwrapped() gives them; a pause without its end lasts on)."""
    pauses, since = [], None
    for t, line in lines:
        if line[9:].startswith("the sim is paused (") and since is None:
            since = t
        elif line[9:].startswith("the sim runs again (") and since is not None:
            pauses.append((since, t))
            since = None
    if since is not None:
        pauses.append((since, float("inf")))
    return lambda t: t - sum(max(0, min(t, b) - a) for a, b in pauses)


def weather_changes(captures: list[str]) -> list[str]:
    """The clouds that changed while (or between) the logs were taken: the add-on's METAR stations fading
    in (weather.log, this session: after its last "weather feed starting"), each from its line on for
    its fade (CROSSFADE_S when the line doesn't say), on the weather's clock (weather_clock: a fade holds
    while the sim is paused); a change at once, between the first log and the last. The logs' times from
    events.log (this session)."""
    try:
        events = (LOGS / "events.log").read_text(encoding="utf-8", errors="replace").splitlines()
        weather = (LOGS / "weather.log").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    when = {}
    for t, line in unwrapped(events):
        if m := re.match(r"\S+ GPU times (\d+):", line):
            when[m[1]] = t
    times = [when[c] for c in captures if c in when]
    if len(times) < len(captures) or not times:
        return ["(some logs aren't in this session's events.log: no weather check)"]
    start = max((i for i, l in enumerate(weather) if l[9:].startswith("weather feed starting")), default=0)
    first, last = min(times), max(times)
    lines = list(unwrapped(weather[start:]))
    w = weather_clock(lines)
    changes = []  # (start, fade, text)
    for t, line in lines:
        m = re.match(r"\S+ cloud anchor (\S+)", line)
        if not m:
            continue
        if f := re.search(r"(?:fading in|crossfad(?:es|ing)) over (\d+) s", line):
            fade = int(f[1])
        elif "updated]" in line:
            fade = CROSSFADE_S
        elif "in use at once" in line:
            fade = 0
        else:
            continue
        if "after a jump" in line:  # (a new flight or a jump: what faded in before is gone)
            changes = [c for c in changes if c[0] >= t]
        changes.append((t, fade, f"{m[1]} {line[:8]} ({'at once' if not fade else f'fading over {fade} s'})"))
    wf, wl = w(first), w(last)
    return [text for t, fade, text in changes
            if (first <= t <= last if not fade else max(wf, w(t)) < min(wl, w(t) + fade))]


def per_call(capture: str, entry: str = CLOUD_PASS) -> list[float]:
    """The entry's own times (us) in one log; [] if the log caught no call (a fragment of a frame)."""
    with open(CAPTURES / f"{capture}_draws.tsv", encoding="utf-8", errors="replace") as f:
        return [float(r["gpu us"]) for r in csv.DictReader(f, delimiter="	")
                if r["entry"] == entry and (r.get("gpu us") or "-") != "-"]


def load(capture: str) -> tuple[dict, dict, float, int]:
    """Per entry {name: [us, count]}, per (entry, crc) the same, the total, and the frames."""
    by_entry, by_crc = defaultdict(lambda: [0.0, 0]), defaultdict(lambda: [0.0, 0])
    total, frames, timed = 0.0, 0, 0
    with open(CAPTURES / f"{capture}_draws.tsv", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            if row["kind"] not in ("draw", "dispatch"):
                continue
            frames += row["entry"] == "ps_deferredRendering"
            us = row.get("gpu us") or "-"
            if us == "-":
                continue
            timed += 1
            t = float(us)
            total += t
            for table, key in ((by_entry, row["entry"]), (by_crc, (row["entry"], row["crc32"]))):
                table[key][0] += t
                table[key][1] += 1
    if timed == 0:
        raise SystemExit(f"capture {capture}: no GPU times in its draw log (an add-on from before 2 Nov 2026?)")
    return by_entry, by_crc, total, max(frames, 1)


def timeline(capture: str, gaps: int) -> None:
    """The GPU's timeline per queue ("gpu at us", logs from 3 Nov 2026 on): its span from
    the first timestamp to the last, the time it was busy (the entries' intervals joined),
    and the longest idle gaps with what ran before and after them (an idle GPU waits for
    the CPU to submit, or for another queue)."""
    with open(CAPTURES / f"{capture}_draws.tsv", encoding="utf-8", errors="replace") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    if not rows or "gpu at us" not in rows[0]:
        return
    queue_of = {}
    for r in rows:
        if r["kind"] == "execute":
            queue_of.setdefault(r["command list"], r["what"].removeprefix("queue "))
    spans = defaultdict(list)  # queue -> [(start, end, what)]
    for r in rows:
        if r["kind"] in ("draw", "dispatch") and r["gpu us"] != "-" and r["gpu at us"] != "-":
            start = float(r["gpu at us"])
            spans[queue_of.get(r["command list"], "?")].append(
                (start, start + float(r["gpu us"]), f"{r['entry']} {r['crc32']} (list {r['command list'][-6:]})"))
    for queue, items in sorted(spans.items(), key=lambda kv: -len(kv[1])):
        items.sort()
        busy, holes = 0.0, []
        start, end, before = items[0]
        for s, e, what in items[1:]:
            if s > end:
                busy += end - start
                holes.append((s - end, before, what))
                start, end, before = s, e, what
            elif e > end:
                end, before = e, what
        busy += end - start
        span = max(e for _, e, _ in items) - items[0][0]
        print(f"  queue {queue[-6:]}: busy {busy / 1000:.2f} ms over a span of {span / 1000:.2f} ms, "
              f"idle {(span - busy) / 1000:.2f} ms in {len(holes)} gap(s)")
        for size, before, after in sorted(holes, reverse=True)[:gaps]:
            print(f"    {size / 1000:6.3f} ms idle after {before}, before {after}")


def load_group(spec: str) -> tuple[dict, dict, float, int]:
    """load() for comma-separated captures, summed (per frame: over all their frames)."""
    by_entry, by_crc, total, frames = {}, {}, 0.0, 0
    for capture in spec.split(","):
        e, c, t, f = load(capture)
        for mine, theirs in ((by_entry, e), (by_crc, c)):
            for k, (us, n) in theirs.items():
                acc = mine.setdefault(k, [0.0, 0])
                acc[0] += us
                acc[1] += n
        total += t
        frames += f
    return by_entry, by_crc, total, max(frames, 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("captures", nargs="+")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--gaps", type=int, default=8, help="idle gaps listed per queue")
    args = ap.parse_args()
    replaced = {p.stem.lower() for p in (ROOT / "build" / "shaders").glob("0x*.cso")}
    runs = [load_group(c) for c in args.captures[:2]]
    for c, (_, _, total, frames) in zip(args.captures, runs):
        print(f"capture {c}: {total / 1000 / frames:.2f} ms of GPU time per frame ({frames} frame(s) logged)")
        if "," not in c:
            timeline(c, args.gaps)
    # the cloud renderer per call: whole frames vary with what each log caught; this doesn't
    for c in args.captures[:2]:
        calls = {x: per_call(x) for x in c.split(",")}
        got = [t for ts in calls.values() for t in ts]
        missed = [x for x, ts in calls.items() if not ts]
        print(f"{CLOUD_PASS} per call, {c}: " + (f"{sum(got) / len(got) / 1000:.3f} ms ({min(got) / 1000:.3f}-"
              f"{max(got) / 1000:.3f}, {len(got)} call(s))" if got else "no call logged")
              + (f"; without it: {', '.join(missed)}" if missed else ""))
    changes = weather_changes([x for c in args.captures[:2] for x in c.split(",")])
    if changes:
        print("WEATHER: the clouds changed during or between these logs, so they don't compare cleanly:")
        for line in changes:
            print("   ", line)
    if len(runs) == 1:
        by_entry, by_crc, _, frames = runs[0]
        print(f"\n{'ms/frame':>9} {'count':>6}  pass")
        for name, (us, n) in sorted(by_entry.items(), key=lambda kv: -kv[1][0])[:args.top]:
            print(f"{us / 1000 / frames:9.3f} {n // frames:6d}  {name}")
        return
    (e0, c0, t0, f0), (e1, c1, t1, f1) = runs
    print(f"difference: {(t1 / f1 - t0 / f0) / 1000:+.2f} ms per frame")
    for title, a, b, label in (("per pass", e0, e1, lambda k: k),
                               ("per shader", c0, c1, lambda k: f"{k[0]} {k[1]}{'*' if k[1] in replaced else ''}")):
        keys = set(a) | set(b)
        rows = sorted(keys, key=lambda k: -abs(b.get(k, [0, 0])[0] / f1 - a.get(k, [0, 0])[0] / f0))
        print(f"\n{title}, by the difference ({args.captures[0]} -> {args.captures[1]}):")
        print(f"{'first':>9} {'second':>9} {'diff':>9} {'count':>11}  ms per frame")
        for k in rows[:args.top]:
            (u0, n0), (u1, n1) = a.get(k, [0.0, 0]), b.get(k, [0.0, 0])
            print(f"{u0 / 1000 / f0:9.3f} {u1 / 1000 / f1:9.3f} {(u1 / f1 - u0 / f0) / 1000:+9.3f} "
                  f"{n0 // f0:5d}/{n1 // f1:<5d}  {label(k)}")


if __name__ == "__main__":
    main()

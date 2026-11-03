"""Where the GPU's time goes: a capture's draw log (captures/<id>_draws.tsv,
"gpu us" column), per pass, or two captures side by side (stock, then mod).

    python tools/gpu_times.py 352               # one log
    python tools/gpu_times.py 352 353           # stock (Ctrl+Shift+F9 off) vs mod
    python tools/gpu_times.py 352 353 --top 40  # more rows

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
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAPTURES = ROOT / "build" / "logs" / "captures"


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


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("captures", nargs="+")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--gaps", type=int, default=8, help="idle gaps listed per queue")
    args = ap.parse_args()
    replaced = {p.stem.lower() for p in (ROOT / "build" / "shaders").glob("0x*.cso")}
    runs = [load(c) for c in args.captures[:2]]
    for c, (_, _, total, frames) in zip(args.captures, runs):
        print(f"capture {c}: {total / 1000 / frames:.2f} ms of GPU time per frame ({frames} frame(s) logged)")
        timeline(c, args.gaps)
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

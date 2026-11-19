"""The cost profile of the mod's cloud renderer, with the A/B mode (tools/ab.py): the full mod against the sim's
own shaders, and against sets with one feature left out (tools/build_ab.py --leave-out), several tests each,
in rounds (every comparison once per round, so the scene's drift spreads over all of them). Prints each
feature's cost in the cloud pass, the mean and spread over its tests, and saves the table to
build/logs/captures/profile_<time>.tsv. Developer installs only, like tools/ab.py; the same measuring setup:
the preset scene, the sim's full pause, full screen, the sim in front for the whole run (it waits when it isn't).

    python tools/ab_profile.py --build             # build the sets (build/ab/p-*; the sim may run)
    python tools/ab_profile.py                     # the profile, 3 rounds (about 25 min)
    python tools/ab_profile.py --rounds 4 --only haze,types

Each row's cost is A - B, as a share of A's cloud pass: B leaves the feature out of A. The cloud types build on
the optical-thickness and cloud-shape patches, and the in-cloud structure on the optical thickness, so the cloud
shape is measured against the mod without types and the optical thickness against the mod without types and
in-cloud structure; the in-cloud structure goes with its fog record (which records it). Without the cloud shape,
the plane light gets the glory's and the shadow's terms as off (patch_dxil.patch_clouds). "noise" is a set against a
copy of itself: what a cost of that size means.

The view flickers hard during the tests whose sets look different (the sim's own shaders, untiling, optical
thickness, cloud shape, types, haze colour, ...): the A/B mode draws the two sets on alternate frames. Expected,
and harmless for the result; looking away is fine (the sim only has to stay in front).
"""
import argparse
import math
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import ab  # noqa: E402

# (row, A, B, B's features left out; None: the sim's own shaders). The full mod is p-full.
SETS = {"p-full": "", "p-stock": None, "p-haze": "haze", "p-untile": "untile", "p-detail": "detail",
        "p-types": "types", "p-ms": "ms", "p-phase": "phase", "p-tilt": "tilt", "p-dump": "dump",
        "p-fogrec": "fogrec", "p-inside": "inside,fogrec", "p-types-inside": "types,inside,fogrec",
        "p-types-optical": "types,optical,inside,fogrec", "p-types-shape": "types,shape"}
ROWS = [
    ("whole mod", "p-full", "p-stock"),
    ("haze colour", "p-full", "p-haze"),
    ("untiling", "p-full", "p-untile"),
    ("surface detail", "p-full", "p-detail"),
    ("cloud types", "p-full", "p-types"),
    ("multiple scattering", "p-full", "p-ms"),
    ("forward lobe", "p-full", "p-phase"),
    ("in-cloud structure + fog record", "p-full", "p-inside"),
    ("fog record", "p-full", "p-fogrec"),
    ("tilt view (developers)", "p-full", "p-tilt"),
    ("capture dump (developers)", "p-full", "p-dump"),
    ("optical thickness (without types, in-cloud structure)", "p-types-inside", "p-types-optical"),
    ("cloud shape: powder, glory, shadow, edges (without types)", "p-types", "p-types-shape"),
    ("noise (the same shaders)", "p-full", "p-null"),
]
KEYS = {"whole mod": "stock", "haze colour": "haze", "untiling": "untile", "surface detail": "detail",
        "cloud types": "types", "multiple scattering": "ms", "forward lobe": "phase",
        "in-cloud structure + fog record": "inside", "fog record": "fogrec", "tilt view (developers)": "tilt",
        "capture dump (developers)": "dump", "optical thickness (without types, in-cloud structure)": "optical",
        "cloud shape: powder, glory, shadow, edges (without types)": "shape", "noise (the same shaders)": "noise"}
RESULT = re.compile(r"A (\d+\.\d+) ms, B (\d+\.\d+) ms per frame.*?B - A ([+-]\d+\.\d+) ms")


def build(parallel: int) -> None:
    def one(item):
        name, leave_out = item
        cmd = [sys.executable, str(ROOT / "tools" / "build_ab.py"), name]
        cmd += ["--stock"] if leave_out is None else (["--leave-out", leave_out] if leave_out else [])
        run = subprocess.run(cmd, capture_output=True, text=True)
        return name, run.returncode, (run.stdout + run.stderr).strip().splitlines()[-1:]
    with ThreadPoolExecutor(parallel) as pool:
        for name, code, last in pool.map(one, SETS.items()):
            print(f"{name}: {'ok' if code == 0 else 'FAILED'} {last[0] if last else ''}", flush=True)
            if code:
                raise SystemExit(1)
    shutil.rmtree(ab.AB / "p-null", ignore_errors=True)
    shutil.copytree(ab.AB / "p-full", ab.AB / "p-null")  # (the add-on refuses a set against itself)
    print("p-null: a copy of p-full")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--build", action="store_true", help="build the sets, then stop")
    ap.add_argument("--parallel", type=int, default=3, help="with --build: sets built at once")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--frames", type=int, default=600)
    ap.add_argument("--only", default="", help="rows by key, comma separated: " + ",".join(KEYS.values()))
    args = ap.parse_args()
    if args.build:
        build(args.parallel)
        return
    only = {k.strip() for k in args.only.split(",") if k.strip()}
    if only - set(KEYS.values()):
        raise SystemExit(f"unknown rows {sorted(only - set(KEYS.values()))}; the keys: {', '.join(KEYS.values())}")
    rows = [r for r in ROWS if not only or KEYS[r[0]] in only]
    for name in {s for _, a, b in rows for s in (a, b)}:
        if not any((ab.AB / name).glob("*.cso")):
            raise SystemExit(f"no shaders in {ab.AB / name}: python tools/ab_profile.py --build")
    if not ab.sim_running():
        raise SystemExit("The sim isn't running.")
    results = {r[0]: [] for r in rows}
    total = len(rows) * args.rounds
    done = 0
    for rnd in range(args.rounds):
        for label, a, b in rows:
            ab.wait_for_front(timeout=900)
            text = ab.run_once(b, a, args.frames, timeout=60 + args.frames / 10)
            done += 1
            # (the working tree's statistics, the "analysed" line, if there is one)
            m = RESULT.search(text.splitlines()[-1]) or RESULT.search(text)
            if not m:
                print(f"[{done}/{total}] {label}: no result: {text[:200]}", flush=True)
                continue
            a_ms, b_ms, diff = float(m[1]), float(m[2]), float(m[3])
            cost = -diff / a_ms * 100.0
            results[label].append((a_ms, -diff, cost))
            test = text.split(":", 1)[0]
            print(f"[{done}/{total}] {test} {label}: {cost:+.1f} % ({-diff:+.3f} ms of {a_ms:.3f})", flush=True)

    stamp = time.strftime("%Y%m%d-%H%M")
    out = ab.CAPTURES / f"profile_{stamp}.tsv"
    lines = ["row\tA\tB\ttests\tcost_ms\tcost_pct\tsd_pct\teach_pct"]
    print(f"\nCost in the cloud pass (A - B, of A), {args.rounds} round(s):")
    for label, a, b in rows:
        got = results[label]
        if not got:
            print(f"  {label:58s} no results")
            continue
        pct = [c for _, _, c in got]
        ms = sum(d for _, d, _ in got) / len(got)
        mean = sum(pct) / len(pct)
        sd = math.sqrt(sum((p - mean) ** 2 for p in pct) / (len(pct) - 1)) if len(pct) > 1 else float("nan")
        each = ", ".join(f"{p:+.1f}" for p in pct)
        print(f"  {label:58s} {mean:+5.1f} % ({ms:+.3f} ms)  sd {sd:.1f}  [{each}]")
        lines.append(f"{label}\t{a}\t{b}\t{len(got)}\t{ms:.4f}\t{mean:.2f}\t{sd:.2f}\t{each}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nsaved {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

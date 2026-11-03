"""Check the fog of what MSFS draws after its clouds (patch_dxil.patch_forward_fog:
ph_base, ph_particle, the light points) and the merge's part (patch_fog_scale).

    python tools/test_forward_fog.py               # the variants seen drawing after the clouds, and a sample
    python tools/test_forward_fog.py 0xf88a2c03 ... # some ph_base variants

Evaluates the patched IR with the float32 interpreter, the fog record
(FOG_REGION), the merge's factor (FOG_SCALE) and fog colour grid (FOG_GRID),
the fragment's view depth and place on the screen (or a light's distance) and
the outputs seeded, and compares every output with
  d = the view depth times sqrt(1 + ((2u - 1) / P00)^2 + ((2v - 1) / P11)^2)
      (the view depth alone without the Global cbuffer; a light's distance),
  T = exp(-sigma clamp(d - NEAR_FOG_INNER_M, 0, NEAR_FOG_OUT_M)),
  sigma = the fresh points' extinction / points (those that see the cockpit,
  their sample within FOG_COCKPIT_M, left out),
  L = the grid bilinear at the place on the screen over the cells with cloud
  enough (FOG_GRID_ALPHA), else the fresh points' radiance bilinear by the
  place (held beyond them; weighted by their extinction without the place)
  times the merge's factor (0 before the merge has run), and by the target and
  blending (DebugCapture bits 10-12): dual source T c + L (1 - T) (1 - tint),
  alpha T c + L (1 - T), premultiplied T c + L (1 - T) a, additive T c,
  refraction buffer all four times T;
exactly the stock output without a mode, without the debug buffer (bit 6) or
within NEAR_FOG_INNER_M (the guard skips the fog there); with FOG_TINT_BIT
the mark (magenta along the ray, else cyan) beyond it.
Screen glare, flares and masks (no cloud fade) are left as they are. The
merge: its factor Global[86].x Global[86].y User[0].z^2 (User[0].x User[0].z
in the larger variants), and the grid's colour over alpha.
"""
import csv
import random
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import patch_dxil as P  # noqa: E402
from test_medium_patch import Evaluator, F  # noqa: E402

SEEN = ["0xf88a2c03", "0xff9b6063", "0x17a06b57",  # drawn after the clouds (capture 285)
        "0x425ce2ad", "0x6d6c9172",  # the refraction buffer (777, 288-290)
        "0x34af57d4",  # a light glow
        "0x2f933fc0", "0x671f4c45",  # screen glare, a refraction mask: left as they are
        "0x0f19769e",  # the 777's cockpit glass (316)
        "0x87278610"]  # the 777's fan discs
OTHERS = [("0x8cbef692", {"world_only": False}), ("0x1853a691", {"world_only": False}),  # particles
          ("0x5317b8af", {"world_only": False, "distance": "light"}),  # light points
          ("0xc133af59", {"world_only": False, "distance": "light"})]  # city lights
SAMPLE = 25
N = 4001
FRAME = 40000
SCALE = 2.5  # the merge's factor in the record
WIDTH, HEIGHT = 2560, 1440
GLOBAL = np.zeros((300, 4))
GLOBAL[105] = (WIDTH, HEIGHT, 1.0 / WIDTH, 1.0 / HEIGHT)  # the render size and its reciprocals
GLOBAL[170, 0], GLOBAL[171, 1] = 1.131055, 2.010765  # the projection's scale (capture 316, cockpit)
NX, NY = P.FOG_GRID_N
_g = np.random.default_rng(11)
GRID = (_g.uniform(0.5, 3.0, (NY, NX, 3)),  # the merge's fog colour per cell ...
        np.where(_g.uniform(0, 1, (NY, NX)) < 0.2, _g.uniform(0, 0.1, (NY, NX)), _g.uniform(0.1, 1, (NY, NX))))


def record(points, scale=SCALE, grid=None):
    """The debug buffer with the fog record: points [(sigma, (r, g, b), age or None[, distance])], the merge's
    factor (None: not written) and fog colour grid ((colour [NY, NX, 3], alpha [NY, NX]) or None: empty)."""
    words = np.zeros((P.FOG_GRID + NX * NY * 16) // 4, np.float32)
    bits = words.view(np.uint32)
    for p, (sigma, light, age, *t) in enumerate(points):
        base = (P.FOG_REGION + p * P.FOG_POINT_BYTES) // 4
        words[base:base + 4] = (sigma, *light)
        bits[base + 4] = 0 if age is None else ((FRAME - age) & 0xFFFF) | 0x10000
        words[base + 5] = t[0] if t else 20.0
    if scale is not None:
        words[P.FOG_SCALE // 4] = scale
        bits[P.FOG_SCALE // 4 + 1] = FRAME | 0x10000
    if grid is not None:
        cells = words[P.FOG_GRID // 4:P.FOG_GRID // 4 + NX * NY * 4].reshape(NY, NX, 4)
        cells[..., :3], cells[..., 3] = grid
    return words


CASES = [  # (name, points, mode, bound[, the merge's factor[, its grid]])
    ("all fresh, dual", [(0.04, (1.0, 1.1, 1.2), 0), (0.06, (0.8, 0.9, 1.0), 1), (0.05, (1.2, 1.2, 1.3), 2),
                         (0.03, (0.9, 0.9, 0.9), 0)], P.FOG_DUAL, True),
    ("all fresh, alpha", [(0.04, (1.0, 1.1, 1.2), 0)] * 4, P.FOG_ALPHA, True),
    ("all fresh, premultiplied", [(0.08, (0.5, 0.6, 0.7), 1)] * 4, P.FOG_PREMULTIPLIED, True),
    ("all fresh, additive", [(0.08, (0.5, 0.6, 0.7), 1)] * 4, P.FOG_ADDITIVE, True),
    ("refraction buffer", [(0.08, (0.5, 0.6, 0.7), 1)] * 4, P.FOG_REFRACTION, True),
    ("stale and unwritten points", [(0.05, (1.0, 1.0, 1.0), 0), (0.5, (9.0, 9.0, 9.0), P.FOG_FRESH_FRAMES + 3),
                                    (0.5, (9.0, 9.0, 9.0), None), (0.02, (0.4, 0.5, 0.6), P.FOG_FRESH_FRAMES)],
     P.FOG_DUAL, True),
    ("none fresh", [(0.5, (9.0, 9.0, 9.0), 50)] * 4, P.FOG_ALPHA, True),
    ("no mode", [(0.05, (1.0, 1.0, 1.0), 0)] * 4, 0, True),
    ("no debug buffer", [(0.05, (1.0, 1.0, 1.0), 0)] * 4, P.FOG_DUAL, False),
    ("before the merge has run", [(0.04, (1.0, 1.1, 1.2), 0)] * 4, P.FOG_ALPHA, True, None, GRID),
    ("two points see the cockpit", [(0.16, (1.0, 1.1, 1.2), 0, 18.0), (0.15, (0.9, 1.0, 1.1), 1, 20.0),
                                    (0.005, (0.8, 0.9, 1.0), 0, 0.8), (0.005, (0.7, 0.8, 0.9), 0, 1.2)], P.FOG_DUAL, True),
    ("the merge's fog colour, alpha", [(0.06, (1.0, 1.1, 1.2), 0)] * 4, P.FOG_ALPHA, True, SCALE, GRID),
    ("the merge's fog colour, dual", [(0.06, (1.0, 1.1, 1.2), 0)] * 4, P.FOG_DUAL, True, SCALE, GRID),
    ("a NaN and an infinity in the grid", [(0.06, (1.0, 1.1, 1.2), 0)] * 4, P.FOG_DUAL, True, SCALE, "bad"),
]


def sites(patched: str) -> list:
    """Per patched output site: per colour channel the new value and the stock value, the tint (RT1), the
    stock alpha and its new value, as the shader has them."""
    lines = patched.splitlines()
    out = {}

    def stock_of(value):  # (our outputs are the guard's phis: [ fogged, go ], [ stock, the block before ])
        return next(re.match(r"  \S+ = phi float \[ \S+, \S+ \], \[ (\S+), \S+ \]$", l)[1] for l in lines
                    if l.startswith(f"  {value} = phi float "))
    for i, line in enumerate(lines):
        if (m := P.STORE.match(line)) and m[3].startswith("%mod.ff"):
            n = int(re.match(r"%mod\.ff(\d+)\.", m[3])[1])
            site = out.setdefault(n, {"n": n})
            if int(m[1]) == 0 and int(m[2]) < 3:
                site.setdefault("rgb", {})[int(m[2])] = (m[3], stock_of(m[3]))
            elif int(m[1]) == 0:
                site["alpha"], site["alpha_new"] = stock_of(m[3]), m[3]
    for n, site in out.items():
        # the site's other outputs: the stores of its block (split by our guard)
        i = next(k for k, l in enumerate(lines) if (m := P.STORE.match(l)) and m[3] == site["rgb"][0][0])
        j, k, stores = i, i, {}
        while not re.match(r"; <label>:|(?!mod\.)[\w.]+:\s*$", lines[j]) and not lines[j].startswith("define "):
            j -= 1
        while lines[k].strip() != "ret void":
            k += 1
        for line2 in lines[j:k]:
            if s := P.STORE.match(line2):
                stores[(int(s[1]), int(s[2]))] = s[3]
        site.setdefault("alpha", stores.get((0, 3), "1.000000e+00"))
        site["tint"] = [stores.get((1, c), "0.000000e+00") for c in range(3)]
    return [out[n] for n in sorted(out)]


def value(got, name):
    return got(name).astype(np.float64) if name.startswith("%") else float(P.literal_value(name))


def check(crc: str, stock: str, args: dict | None = None) -> list[str]:
    args = args or {}
    try:
        patched, summary = P.patch_forward_fog(stock, **args)
    except P.SkipPatch as e:
        print(f"SKIP {crc}: {e}")
        return []
    rng = np.random.default_rng(5)
    failures = []
    found = sites(patched)
    if not found or any(sorted(s.get("rgb", {})) != [0, 1, 2] for s in found):
        return [f"{crc}: output sites not found ({summary})"]
    depth = np.concatenate([[0.0, 1.0, 1.5, 2.0, 10.0, 299.0, 301.5, 1e4], rng.uniform(0, 400, N - 8)]).astype(F)
    light_d = args.get("distance") == "light"
    for site in found:
        n = site["n"]
        screen = "along the ray" in summary  # (the Global cbuffer: the ray's length, the colour by place)
        if light_d:  # the light's distance
            seeds = {f"%mod.ff{n}.d": depth}
            d, uv = depth.astype(np.float64), None
        else:  # the view depth and the place on the screen (pixels of a 2560 x 1440 frame)
            seeds = {f"%mod.ff{n}.w": depth, f"%mod.ff{n}.px": rng.uniform(-50, WIDTH + 50, N).astype(F),
                     f"%mod.ff{n}.py": rng.uniform(-50, HEIGHT + 50, N).astype(F)}
            uv = [seeds[f"%mod.ff{n}.p{a}"].astype(np.float64) / size for a, size in (("x", WIDTH), ("y", HEIGHT))]
            d = depth.astype(np.float64)
            if screen:
                d = d * np.minimum(np.sqrt(1 + ((2 * uv[0] - 1) / GLOBAL[170, 0]) ** 2
                                       + ((2 * uv[1] - 1) / GLOBAL[171, 1]) ** 2), P.FOG_RAY_MAX)
        for c in range(3):
            if site["rgb"][c][1].startswith("%"):  # (else a constant colour)
                seeds[site["rgb"][c][1]] = rng.uniform(0, 4, N).astype(F)
            if site["tint"][c].startswith("%"):
                seeds[site["tint"][c]] = rng.uniform(0, 1, N).astype(F)
        if site["alpha"].startswith("%"):
            seeds[site["alpha"]] = rng.uniform(0, 1, N).astype(F)
        for name, points, mode, bound, *rest in CASES:
            scale = rest[0] if rest else SCALE
            grid = rest[1] if len(rest) > 1 else None
            if isinstance(grid, str):  # every 3rd cell NaN, every 5th infinite: read as 0 and 1e6
                bad = GRID[0].copy()
                bad.reshape(-1, 3)[::3] = np.nan
                bad.reshape(-1, 3)[::5] = np.inf
                grid = (bad, GRID[1])
            flags = (64 if bound else 0) | (mode << P.FOG_MODE_SHIFT) | (FRAME << 16)
            mod = np.zeros((4, 4))
            mod[0, 0] = np.uint32(flags & 0xFFFFFFFF).view(np.float32)
            got = Evaluator(patched, {"b0 space50": mod, 4800: GLOBAL}, seeds, buffer=record(points, scale, grid))
            fresh = [age is not None and age <= P.FOG_FRESH_FRAMES and bound and mode != 0 for _, _, age, *_ in points]
            sig = [max(s, 0.0) if f else 0.0 for (s, *_), f in zip(points, fresh)]
            # the points that see the cockpit are left out of the extinction
            cockpit = [f and (t[0] if t else 20.0) < P.FOG_COCKPIT_M for (_, _, _, *t), f in zip(points, fresh)]
            sigma = sum(x for x, c in zip(sig, cockpit) if not c) / max(len(points) - sum(cockpit), 1)
            light = [np.full(N, sum(s * l[c] for s, (_, l, *_) in zip(sig, points)) / max(sum(sig), 1e-12))
                     * (scale or 0.0) for c in range(3)]
            if screen:
                # bilinear between the fresh points by the place on the screen, held beyond them
                st = [np.clip((uv[k] - 0.3) / 0.4, 0, 1) for k in (0, 1)]
                wsum, lsum = np.zeros(N), [np.zeros(N) for _ in range(3)]
                for (qx, qy), f, (_, l, *_) in zip(P.FOG_POINTS, fresh, points):
                    w = (st[0] if qx > 0.5 else 1 - st[0]) * (st[1] if qy > 0.5 else 1 - st[1]) * f
                    wsum += w
                    for c in range(3):
                        lsum[c] += w * l[c]
                light = [np.where(wsum > 1e-4, lsum[c] / np.maximum(wsum, 1e-4) * (scale or 0.0), light[c])
                         for c in range(3)]
                # ... or the merge's grid, over the cells with cloud enough
                if grid is not None and scale is not None and bound and mode != 0:
                    gx, gy = uv[0] * NX - 0.5, uv[1] * NY - 0.5
                    ix, iy = np.clip(np.floor(gx), 0, NX - 2), np.clip(np.floor(gy), 0, NY - 2)
                    fx, fy = np.clip(gx - ix, 0, 1), np.clip(gy - iy, 0, 1)
                    ix, iy = ix.astype(int), iy.astype(int)
                    gw, gs = np.zeros(N), [np.zeros(N) for _ in range(3)]
                    lo, hi = P.FOG_GRID_ALPHA
                    for dy in (0, 1):
                        for dx in (0, 1):
                            a = grid[1][iy + dy, ix + dx]
                            w = (fx if dx else 1 - fx) * (fy if dy else 1 - fy) * np.clip((a - lo) / (hi - lo), 0, 1)
                            gw += w
                            for c in range(3):
                                cell = np.nan_to_num(grid[0][iy + dy, ix + dx, c], nan=0.0, posinf=P.FOG_COLOUR_MAX)
                                gs[c] += w * np.clip(cell, 0, P.FOG_COLOUR_MAX)
                    light = [np.where(gw > 1e-3, gs[c] / np.maximum(gw, 1e-3), light[c]) for c in range(3)]
            T = np.exp(-sigma * np.clip(d - P.NEAR_FOG_INNER_M, 0, P.NEAR_FOG_OUT_M))
            alpha = value(got, site["alpha"])
            for c in range(3):
                new, old = site["rgb"][c]
                stock_c = seeds[old] if old.startswith("%") else np.full(N, float(P.literal_value(old)), F)
                src = stock_c.astype(np.float64)
                out = got(new).astype(np.float64)
                if mode == 0 or not bound:
                    if not np.array_equal(out.astype(F), stock_c):
                        failures.append(f"{crc} {name}: channel {c} not stock")
                    continue
                near = got(f"%mod.ff{n}.d") <= P.NEAR_FOG_INNER_M  # (the guard skips it all: bit for bit)
                if not np.array_equal(out.astype(F)[near], stock_c[near]):
                    failures.append(f"{crc} {name}: channel {c} not stock within {P.NEAR_FOG_INNER_M} m")
                tint = value(got, site["tint"][c])
                fog = np.where(T < 1, light[c] * (1 - T), 0.0)
                ref = {P.FOG_DUAL: T * src + fog * (1 - tint), P.FOG_ALPHA: T * src + fog,
                       P.FOG_PREMULTIPLIED: T * src + fog * alpha, P.FOG_ADDITIVE: T * src,
                       P.FOG_REFRACTION: T * src}[mode]
                err = float(np.max(np.abs(out - ref) / np.maximum(np.abs(ref), 1e-3)))
                if err > 1e-4:
                    failures.append(f"{crc} {name}: channel {c} rel. error {err:.1e}")
            if "alpha_new" in site:  # the refraction buffer's coverage fades too; else unchanged
                a_out = got(site["alpha_new"]).astype(np.float64)
                a_ref = T * alpha if (mode == P.FOG_REFRACTION and bound) else alpha + np.zeros(N)
                if float(np.max(np.abs(a_out - a_ref))) > 1e-5:
                    failures.append(f"{crc} {name}: alpha abs. error {float(np.max(np.abs(a_out - a_ref))):.1e}")
        # the developers' tint (C35): magenta along the ray, cyan with the depth or the light's distance alone
        mark = (1.0, 0.0, 1.0) if screen else (0.0, 1.0, 1.0)
        for mode in (0, P.FOG_ALPHA):
            mod = np.zeros((4, 4))
            mod[0, 0] = np.uint32(64 | P.FOG_TINT_BIT | (mode << P.FOG_MODE_SHIFT) | (FRAME << 16)).view(np.float32)
            got = Evaluator(patched, {"b0 space50": mod, 4800: GLOBAL}, seeds, buffer=record(CASES[0][1]))
            beyond = got(f"%mod.ff{n}.d") > P.NEAR_FOG_INNER_M
            for c in range(3):
                new, old = site["rgb"][c]
                stock_c = seeds[old] if old.startswith("%") else np.full(N, float(P.literal_value(old)), F)
                want = np.where(beyond, F(mark[c]), stock_c) if mode else stock_c
                if not np.array_equal(got(new).astype(F), want):
                    failures.append(f"{crc} tint, mode {mode}: channel {c} not {'the mark' if mode else 'stock'}")
    print(f"{'FAIL' if failures else 'PASS'} {crc}: {summary}")
    return failures


def check_merge(crc: str, stock: str) -> list[str]:
    """The merge's factor, Global[86].x Global[86].y User[0].z^2, and its grid cell: the clouds' colour over
    their alpha, and the alpha, as patch_fog_scale stores them."""
    patched, summary = P.patch_fog_scale(stock)
    rng = np.random.default_rng(3)
    glob, user = rng.uniform(-4, 4, (300, 4)), rng.uniform(-4, 4, (658, 4))
    cbs = {4800: glob, 10528: user, "b0 space50": np.zeros((4, 4))}
    failures = []
    got = float(Evaluator(patched, cbs, {})("%mod.fs.f"))
    want = glob[86, 0] * glob[86, 1] * user[0, 2] * user[0, 0 if "U[0].x" in summary else 2]
    if abs(got - want) > 1e-5 * max(1.0, abs(want)):
        failures.append(f"{crc}: merge factor {got} != {want}")
    colour, alpha = P.merge_colour(P.Ir(stock))
    store = next(line for line in patched.splitlines() if "rawBufferStore" in line and "i8 15" in line)
    stored = re.findall(r"float (\S+?)[,)]", store.split("i32 undef,", 1)[1])[:4]
    seeds = {v: rng.uniform(0, 5, N).astype(F) for v in colour}
    seeds[alpha] = np.concatenate([[0.0, 1e-4, 1e-3], rng.uniform(0, 1, N - 3)]).astype(F)
    ev = Evaluator(patched, cbs, seeds)
    a64 = seeds[alpha].astype(np.float64)
    for c in range(3):
        ref = np.clip(seeds[colour[c]].astype(np.float64) / np.maximum(a64, 1e-3), 0, P.FOG_COLOUR_MAX)
        err = float(np.max(np.abs(ev(stored[c]).astype(np.float64) - ref) / np.maximum(ref, 1e-3)))
        if err > 1e-5:
            failures.append(f"{crc}: grid colour {c} rel. error {err:.1e}")
    if not np.array_equal(ev(stored[3]).astype(F), seeds[alpha]):
        failures.append(f"{crc}: grid alpha isn't the clouds' alpha")
    print(f"{'FAIL' if failures else 'PASS'} {crc}: {summary}, factor {got:.6g} (want {want:.6g})")
    return failures


def main():
    dxc = P.find_dxc()
    index = list(csv.DictReader(open(ROOT / "extracted" / "index.csv")))
    by_crc = {Path(r["path"]).name.split(".")[0]: r for r in index}
    base = sorted(c for c, r in by_crc.items() if r["entry"] == "ph_base")
    wanted = [(c, None) for c in (sys.argv[1:] or SEEN + random.Random(7).sample(sorted(set(base) - set(SEEN)), SAMPLE))]
    if not sys.argv[1:]:
        wanted += OTHERS
    failed = 0
    for crc, args in wanted:
        problems = check(crc, P.disassemble(ROOT / "extracted" / by_crc[crc]["path"], dxc), args)
        for p in problems[:8]:
            print("   ", p)
        failed += bool(problems)
    if not sys.argv[1:]:
        for crc, row in by_crc.items():
            if row["entry"] == "ph_volumetric_clouds_merge":
                problems = check_merge(crc, P.disassemble(ROOT / "extracted" / row["path"], dxc))
                for p in problems[:8]:
                    print("   ", p)
                failed += bool(problems)
    if failed:
        raise SystemExit(f"{failed} shader(s) failed")


if __name__ == "__main__":
    main()

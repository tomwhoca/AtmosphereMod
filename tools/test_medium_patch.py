"""Check patch_dxil.patch_medium on the shaders it patches.

    python tools/test_medium_patch.py                    # every "medium" target in the manifest
    python tools/test_medium_patch.py ch_sky 0xbc54738a  # one entry, optionally some variants

For each march site the patcher found, evaluates the patched IR's medium
values (Mie density, the scattering and extinction products, the HG phases)
with a small float32 interpreter, from random march segments, and compares:
  - with model mix T0 = 0: bit-identical to the stock IR's values;
  - otherwise: with an independent float64 version of our medium model,
    computed from the same segment.
The segment (origin, direction, t0, t1) and the ozone tent are the
interpreter's inputs; everything between them and the checked values is the
shader's own code, so a wrong match (another value, another segment) fails.
"""

import csv
import json
import re
import struct
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import patch_dxil as P  # noqa: E402

F = np.float32
SAMPLES = 4096
# SkyAtmosphere constants as captured in flight.
ATMO = np.array([
    [0.00229099998, 0.00154056761, 0, 0],
    [0.00656580459, 0.0122912563, 0.0294129997, 0.8],
    [0.008, 0.008, 0.008, 6360],
    [0.008444, 0.008444, 0.008444, 6460],
    [0.000444, 0.000444, 0.000444, -0.5],
    [0.045186203, 0.187820762, 0.456411064, -0.125],
    [25, -0.666666687, 0.0666666701, 1],
    [2.66666675, -0.0666666701, 4, 14],
    [100, 40449600, 41731600, 1132.25439],
    [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]], dtype=np.float64)

# T0..T11 and Extra (T12, T13); T0 = 0 is the stock check.
TUNABLE_SETS = [
    [0, 1.5, 0.15, 4, 0.6, 2.5, 0.05, 0.1, 1, 0.66, 1.3, 1],
    [1, 1.5, 0.15, 4, 0.6, 2.5, 0.05, 0.1, 1, 0.66, 1.3, 1],
    [1, 5.4, 0.3, 12, 1.2, 3.0, 0.4, 0.3, 0, 0.0, 0.3, 1],
    [0.4, 0.8, 0.05, 2, 0.1, 1.5, 0.0, 0.0, 0.5, 0.75, 2.0, 0],
]


# Error floors: the boundary layer's integral above its top cancels to ~1e-7
# (float32 near T3 * T1); absolute errors that small don't matter.
FLOOR = {"mie": 1e-5, "mies": 1e-7, "ray": 1e-7, "ozo": 1e-7, "ext": 1e-7, "phase": 1e-6}


class Leaf(Exception):
    pass


def literal(tok: str):
    if tok.startswith("0x"):
        return F(struct.unpack("<d", struct.pack("<Q", int(tok, 16)))[0])
    return F(float(tok))


class Evaluator:
    """Evaluates values of an IR text on demand (memoised), float32 arrays."""

    def __init__(self, text: str, cbuffers: dict, seeds: dict, texture=None, buffer=None, sampler=None):
        self.rhs = {}
        for line in text.splitlines():
            if m := re.match(r"^\s+(%[\w.]+) = (.*?)(\s+;.*)?$", line):
                self.rhs[m[1]] = m[2]
        self.cbuffers = cbuffers  # "b<n> space<s>" or size in bytes -> array [regs, 4]
        self.env = dict(seeds)
        # optional mocks: texture(x, y, slice) -> float4 rows (textureLoad), and
        # the debug buffer as float32 words (rawBufferLoad; 0 outside)
        self.texture = texture
        self.buffer = buffer
        self.sampler = sampler  # sampler(u, v) -> float4 rows (sampleLevel of 2D textures)

    def ival(self, tok):
        if tok.startswith("%"):
            return self(tok)
        return np.int64(int(tok))

    def integer(self, rhs):
        """i32 arithmetic, casts, texture and raw-buffer loads; None if not one."""
        iv = self.ival
        if m := re.match(r"(add|sub|mul|and|or|xor|shl|lshr|udiv|urem)(?: nuw)?(?: nsw)? i32 (\S+), (\S+)", rhs):
            a, b = np.asarray(iv(m[2]), np.int64), np.asarray(iv(m[3]), np.int64)
            out = {"add": lambda: a + b, "sub": lambda: a - b, "mul": lambda: a * b, "and": lambda: a & b,
                   "or": lambda: a | b, "xor": lambda: a ^ b, "shl": lambda: a << b,
                   "lshr": lambda: (a & 0xFFFFFFFF) >> b, "udiv": lambda: a // b, "urem": lambda: a % b}[m[1]]()
            out = out & 0xFFFFFFFF
            return np.where(out >= 2 ** 31, out - 2 ** 32, out).astype(np.int64)
        if m := re.match(r"icmp (\w+) i32 (\S+), (\S+)", rhs):
            a, b = np.asarray(iv(m[2]), np.int64), np.asarray(iv(m[3]), np.int64)
            ua, ub = a & 0xFFFFFFFF, b & 0xFFFFFFFF
            return {"eq": a == b, "ne": a != b, "ult": ua < ub, "ule": ua <= ub, "ugt": ua > ub, "uge": ua >= ub,
                    "slt": a < b, "sle": a <= b, "sgt": a > b, "sge": a >= b}[m[1]]
        if m := re.match(r"zext i1 (\S+) to i32", rhs):
            return np.asarray(self(m[1])).astype(np.int64)
        if m := re.match(r"select i1 (\S+), i32 (\S+), i32 (\S+)", rhs):
            return np.where(self(m[1]), iv(m[2]), iv(m[3])).astype(np.int64)
        if m := re.match(r"xor i1 (\S+), true", rhs):
            return np.logical_not(self(m[1]))
        if m := re.match(r"bitcast float (\S+) to i32", rhs):
            return np.asarray(self.val(m[1]), np.float32).view(np.int32).astype(np.int64)
        if m := re.match(r"bitcast i32 (\S+) to float", rhs):
            return np.asarray(iv(m[1]), np.int64).astype(np.int32).view(np.float32)
        if m := re.match(r"fptosi float (\S+) to i32", rhs):
            with np.errstate(all="ignore"):
                return np.nan_to_num(np.trunc(self.val(m[1])), nan=0, posinf=2 ** 31 - 1,
                                     neginf=-2 ** 31).astype(np.int64)
        if m := re.match(r"(?:uitofp|sitofp) i32 (\S+) to float", rhs):
            return np.asarray(iv(m[1])).astype(F)
        if re.match(r"call %dx\.types\.Handle @dx\.op\.annotateHandle\(i32 216, %dx\.types\.Handle \S+, "
                    r"%dx\.types\.ResourceProperties \{ i32 (7|4107),", rhs):
            return "resource"
        if m := re.match(r"call %dx\.types\.ResRet\.f32 @dx\.op\.textureLoad\.f32\(i32 66, %dx\.types\.Handle \S+, "
                         r"i32 0, i32 (\S+), i32 (\S+), i32 (\S+),", rhs):
            return self.texture(iv(m[1]), iv(m[2]), iv(m[3]))
        if m := re.match(r"call %dx\.types\.ResRet\.f32 @dx\.op\.rawBufferLoad\.f32\(i32 139, %dx\.types\.Handle \S+, "
                         r"i32 (\S+), i32 undef, i8 \d+, i32 4\)", rhs):
            addr = np.asarray(iv(m[1]), np.int64)
            words = np.asarray(self.buffer if self.buffer is not None else np.zeros(1, F), F)
            idx = addr[..., None] // 4 + np.arange(4)
            ok = (idx >= 0) & (idx < len(words))
            return np.where(ok, words[np.clip(idx, 0, len(words) - 1)], 0).astype(F)
        if m := re.match(r"call %dx\.types\.ResRet\.f32 @dx\.op\.sampleLevel\.f32\(i32 62, %dx\.types\.Handle \S+, "
                         r"%dx\.types\.Handle \S+, float (\S+), float (\S+), float undef,", rhs):
            return np.asarray(self.sampler(self.val(m[1]), self.val(m[2])), F)
        if m := re.match(r"call %dx\.types\.ResRet\.f32 @dx\.op\.sampleLevel\.f32\(i32 62, %dx\.types\.Handle \S+, "
                         r"%dx\.types\.Handle \S+, float (\S+), float (\S+), float (\S+), float undef,", rhs):
            return np.asarray(self.sampler(self.val(m[1]), self.val(m[2]), self.val(m[3])), F)
        if m := re.match(r"call float @dx\.op\.dot2\.f32\(i32 54, float (\S+), float (\S+), float (\S+), float (\S+)\)", rhs):
            return (self.val(m[1]) * self.val(m[3]) + self.val(m[2]) * self.val(m[4])).astype(F)
        if m := re.match(r"extractvalue %dx\.types\.ResRet\.f32 (\S+), (\d)", rhs):
            return self(m[1])[..., int(m[2])]
        return None

    def val(self, tok):
        return self(tok) if tok.startswith("%") else literal(tok)

    def __call__(self, name):
        if name in self.env:
            return self.env[name]
        rhs = self.rhs[name]
        v = self.val
        if m := re.match(r"call %dx\.types\.Handle @dx\.op\.annotateHandle\(i32 216, %dx\.types\.Handle (\S+), "
                         r"%dx\.types\.ResourceProperties \{ i32 13, i32 (\d+) \}\)", rhs):
            # a cbuffer: by binding ("b1 space50") if given one, else by size in bytes
            bind = re.match(r"call %dx\.types\.Handle @dx\.op\.createHandleFromBinding\(i32 217, "
                            r"%dx\.types\.ResBind \{ i32 (\d+), i32 \d+, i32 (\d+), i8 2 \}", self.rhs.get(m[1], ""))
            key = f"b{bind[1]} space{bind[2]}" if bind else None
            out = key if key in self.cbuffers else int(m[2])
        elif m := re.match(r"call %dx\.types\.CBufRet\.f32 @dx\.op\.cbufferLoadLegacy\.f32\(i32 59, "
                           r"%dx\.types\.Handle (\S+), i32 (\S+)\)", rhs):
            size = self(m[1])
            if size not in self.cbuffers:
                raise Leaf(f"{name}: cbuffer of {size} bytes")
            reg = np.asarray(self.ival(m[2]))  # constant, or computed (one value for all lanes)
            if reg.ndim and np.unique(reg).size != 1:
                raise Leaf(f"{name}: cbuffer register varies across lanes")
            # (an index computed from random test constants wraps: stock and patched read alike)
            out = self.cbuffers[size][int(reg.ravel()[0]) % len(self.cbuffers[size])].astype(F)
        elif m := re.match(r"extractvalue %dx\.types\.CBufRet\.f32 (\S+), (\d)", rhs):
            out = self(m[1])[..., int(m[2])]
        elif m := re.match(r"(fmul|fadd|fsub|fdiv)(?: fast)? float (\S+), (\S+)", rhs):
            a, b = v(m[2]), v(m[3])
            with np.errstate(all="ignore"):
                out = {"fmul": a * b, "fadd": a + b, "fsub": a - b, "fdiv": a / b}[m[1]]
        elif m := re.match(r"call float @dx\.op\.unary\.f32\(i32 (\d+), float (\S+)\)", rhs):
            a = v(m[2])
            with np.errstate(all="ignore"):
                out = {"6": np.abs, "7": lambda x: np.clip(x, 0, 1), "12": np.cos, "13": np.sin, "21": np.exp2, "23": np.log2,
                       "24": np.sqrt, "25": lambda x: 1 / np.sqrt(x), "27": np.floor, "22": lambda x: x - np.floor(x),
                       "26": np.rint, "28": np.ceil, "29": np.trunc}[m[1]](a)
        elif m := re.match(r"call float @dx\.op\.binary\.f32\(i32 (35|36), float (\S+), float (\S+)\)", rhs):
            out = (np.maximum if m[1] == "35" else np.minimum)(v(m[2]), v(m[3]))
        elif m := re.match(r"call float @dx\.op\.tertiary\.f32\(i32 46, float (\S+), float (\S+), float (\S+)\)", rhs):
            out = v(m[1]) * v(m[2]) + v(m[3])
        elif m := re.match(r"call float @dx\.op\.dot3\.f32\(i32 55, " + ", ".join([r"float (\S+)"] * 6) + r"\)", rhs):
            out = v(m[1]) * v(m[4]) + v(m[2]) * v(m[5]) + v(m[3]) * v(m[6])
        elif m := re.match(r"fcmp(?: fast)? (\w+) float (\S+), (\S+)", rhs):
            a, b = v(m[2]), v(m[3])
            out = {"olt": a < b, "ole": a <= b, "ogt": a > b, "oge": a >= b, "oeq": a == b,
                   "une": a != b, "ult": ~(a >= b), "ugt": ~(a <= b)}[m[1]]
        elif m := re.match(r"select i1 (\S+), float (\S+), float (\S+)", rhs):
            out = np.where(v(m[1]), v(m[2]), v(m[3]))
        elif m := re.match(r"(and|or) i1 (\S+), (\S+)", rhs):
            out = np.logical_and(v(m[2]), v(m[3])) if m[1] == "and" else np.logical_or(v(m[2]), v(m[3]))
        elif (out := self.integer(rhs)) is not None:
            self.env[name] = out
            return out
        else:
            raise Leaf(f"{name} = {rhs[:100]}")
        if not isinstance(out, (int, str, np.ndarray)):
            out = np.asarray(out)
        if isinstance(out, np.ndarray) and out.dtype != bool:
            out = out.astype(F)
        self.env[name] = out
        return out


# ---------------------------------------------------------------- reference (float64, our medium model)

def reference(t, h0, h1, h, cos_theta, atmo):
    mix = np.clip(t[0], 0, 1)
    top, edge, haze = t[1], max(t[2], 0.001), t[3]
    ft, fts, brown = t[4], max(t[5], 0.001), t[6]
    w, refsky = np.clip(t[7], 0, 1), np.clip(t[8], 0, 1)
    g_ours, ang = np.clip(t[9], 0, 0.95), np.clip(t[10], 0, 3)
    c = atmo

    def bl_density(x):
        return haze / (1 + np.exp((x - top) / edge))

    def bl_integral(x):
        y = (x - top) / edge
        return haze * (x - edge * (np.maximum(y, 0) + np.log1p(np.exp(-np.abs(y)))))

    dh = h1 - h0
    with np.errstate(all="ignore"):
        bl = np.where(np.abs(dh) > 0.01, (bl_integral(h1) - bl_integral(h0)) / np.where(dh == 0, 1, dh),
                      bl_density(h))
    stock_mie = np.exp(c[4, 3] * h)
    dens_mie = stock_mie if mix <= 0 else stock_mie + (bl + ft * np.exp(-h / fts) - stock_mie) * mix
    dens_ray = np.exp(h * c[5, 3])
    reference_mix = mix * refsky
    spectral = (np.exp2(ang * np.array(P.AEROSOL_SPECTRAL_LOG2)) - 1) * mix + 1
    out = {"mie": dens_mie}
    for k in range(3):
        rayleigh = c[1, k] + (P.REFERENCE_RAYLEIGH[k] - c[1, k]) * reference_mix
        ozone = c[0, k] + (P.REFERENCE_OZONE[k] - c[0, k]) * reference_mix
        tent = np.where(h < c[6, 0], c[6, 2] * h + c[6, 1], c[7, 1] * h + c[7, 0])
        out[f"ray{k}"] = dens_ray * rayleigh
        out[f"mies{k}"] = dens_mie * c[2, k] * spectral[k]
        out[f"ozo{k}"] = np.clip(tent, 0, 1) * ozone
        out[f"ext{k}"] = (dens_mie * c[3, k] * spectral[k] + out[f"ray{k}"] + out[f"ozo{k}"]
                          + mix * brown * bl * c[2, 1] * P.BROWN_HAZE_TINT[k])

    def hg(g, x):
        d = 1 + g * g - 2 * g * x
        return (1 - g * g) / (4 * np.pi * d * np.sqrt(d))

    g_stock = c[1, 3]
    if mix <= 0:
        out["phase"] = hg(g_stock, cos_theta)
    else:
        g = (g_ours - g_stock) * mix + g_stock if g_ours > 0 else g_stock
        fwd = hg(g, cos_theta)
        out["phase"] = (hg(P.BACKSCATTER_G, cos_theta) - fwd) * (mix * w) + fwd
    return out


def segments(rng, n, radius, o_free=(0, 1, 2), d_free=(0, 1, 2)):
    """Random march segments around the planet: origin, direction, t0, t1, heights.
    o_free, d_free: the components the shader's origin and direction have (the
    LUT passes march from a point on an axis, in a plane: the others are 0)."""
    up = rng.normal(size=(n, 3))
    up[:, [k for k in range(3) if k not in o_free]] = 0.0
    up /= np.linalg.norm(up, axis=1, keepdims=True)
    side = rng.normal(size=(n, 3))
    side[:, [k for k in range(3) if k not in d_free]] = 0.0
    side -= np.sum(side * up, axis=1, keepdims=True) * up
    side /= np.linalg.norm(side, axis=1, keepdims=True)
    cam = rng.uniform(0.0, 12.0, n)
    elev = np.radians(rng.uniform(-30, 30, n))
    level = rng.random(n) < 0.1  # nearly level, short segments: the midpoint fallback
    elev[level] = np.radians(rng.uniform(-0.01, 0.01, level.sum()))
    origin = up * (radius + cam)[:, None]
    direction = np.cos(elev)[:, None] * side + np.sin(elev)[:, None] * up
    t0 = rng.uniform(0, 60, n)
    t1 = t0 + np.where(level, rng.uniform(0.001, 0.5, n), rng.uniform(0.01, 40, n))
    t0[level] = 0.0
    t1[level] = rng.uniform(0.001, 0.5, level.sum())
    return origin.astype(F), direction.astype(F), t0.astype(F), t1.astype(F)


def check(entry: str, crc: str, stock: str, rng, args: dict) -> list[str]:
    ir = P.Ir(stock)
    sites, phases = P.find_medium(ir, args.get("no_phase", False))
    patched, summary = P.patch_medium(stock, **args)
    failures = []
    worst = 0.0
    radius = F(ATMO[2, 3])
    for tun in TUNABLE_SETS:
        mod = np.zeros((4, 4))
        mod[1:4] = np.array(tun, dtype=np.float64).reshape(3, 4)
        cbs = {208: ATMO, 64: mod}
        for s, site in enumerate(sites):
            t0n, t1n, origin, direction, _ = site["segment"]
            # (components the shader folded away, or that are literally 0, stay 0)
            o_free = [k for k in range(3) if isinstance(origin[k], str)]
            d_free = [k for k in range(3) if isinstance(direction[k], str)]
            o, d, t0, t1 = segments(rng, SAMPLES, radius, o_free, d_free)
            seeds = {t0n: t0, t1n: t1}
            for k in range(3):
                if isinstance(origin[k], str):
                    seeds[origin[k]] = o[:, k]
                if isinstance(direction[k], str):
                    seeds[direction[k]] = d[:, k]
            o64, d64 = o.astype(np.float64), d.astype(np.float64)

            def height(t):
                return np.linalg.norm(o64 + t.astype(np.float64)[:, None] * d64, axis=1) - ATMO[2, 3]
            exact = {"h": height((t0.astype(np.float64) + t1) * 0.5), "h0": height(t0), "h1": height(t1)}
            # The shader's heights (|p| - R in float32, ~1e-3 km off near R = 6360
            # km) feed the reference, so that only the maths after them is compared;
            # they're checked against the exact heights separately.
            bl_heights = re.findall(rf"^\s+%mod\.s{s}a\d+ = fsub fast float (%[\w.]+), %mod\.T1$",
                                    patched, re.M)
            need_names = {"h": site["h"], "h0": bl_heights[0], "h1": bl_heights[1]}
            # The ozone tent's branches join in a phi: seed it from the shader's height.
            ozo_in = ir.unary[site["ozo"]][1]
            if P.PHI.match(ir.lines[ir.defs[ozo_in]]):
                h = Evaluator(stock, cbs, seeds)(site["h"]).astype(np.float64)
                seeds[ozo_in] = np.where(h < ATMO[6, 0], ATMO[6, 2] * h + ATMO[6, 1],
                                         ATMO[7, 1] * h + ATMO[7, 0]).astype(F)
            cos_theta = rng.uniform(-1, 1, SAMPLES).astype(F)
            for name, cos, _ in phases:
                seeds[cos] = cos_theta
            names = {"mie": site["mie"]}
            for k in range(3):
                names[f"ray{k}"] = site["ray_p"][k][0]
                if site["mie_scat_p"]:  # (none in an extinction-only pass)
                    names[f"mies{k}"] = site["mie_scat_p"][k][0]
                names[f"ozo{k}"] = site["ozo_p"][k][0]
                names[f"ext{k}"] = site["ext"][k]
            try:
                got = Evaluator(patched, cbs, seeds)
                values = {key: got(n) for key, n in names.items()}
                values.update({f"phase{p}": got(n) for p, (n, _, _) in enumerate(phases)})
                if tun[0] <= 0:
                    want = Evaluator(stock, cbs, seeds)
                    for key, n in names.items():
                        if not np.array_equal(values[key], want(n), equal_nan=True):
                            failures.append(f"site {s} {key}: T0 = 0 differs from stock")
                    for p, (n, _, _) in enumerate(phases):
                        if not np.array_equal(values[f"phase{p}"], want(n)):
                            failures.append(f"phase {p}: T0 = 0 differs from stock")
                heights = {key: got(n).astype(np.float64) for key, n in need_names.items()}
                for key, value in heights.items():
                    off = float(np.abs(value - exact[key]).max())
                    if off > 2e-3:
                        failures.append(f"site {s} {key}: {off:.1e} km from the segment's exact height")
                ref = reference(tun, heights["h0"], heights["h1"], heights["h"], cos_theta.astype(np.float64), ATMO)
                for key, value in values.items():
                    r = ref["phase" if key.startswith("phase") else key]
                    err = np.abs(value - r) / (np.abs(r) + FLOOR[key.rstrip("0123456789")])
                    e = float(np.percentile(err, 99.9))
                    worst = max(worst, e)
                    # float32: log(1 + e^-|x|) keeps ~1e-7 absolute, which is up to
                    # ~3e-4 of the density in thin segments above the layer's top.
                    # A wrong match is off by O(1).
                    if e > 5e-4 or float(err.max()) > 2e-3:
                        i = int(np.argmax(err))
                        failures.append(f"site {s} {key} T={tun[:4]}: rel. error p99.9 {e:.2e}, max {err.max():.2e} "
                                        f"(h0 {heights['h0'][i]:.4f} h1 {heights['h1'][i]:.4f}: "
                                        f"{float(value[i]):.6e} vs {float(r[i]):.6e})")
            except Leaf as e:
                failures.append(f"site {s}: interpreter reached an input it doesn't know: {e}")
    status = "FAIL" if failures else "PASS"
    print(f"{status} {entry} {crc}: {summary}; worst p99.9 rel. error {worst:.1e}")
    return failures


def main():
    dxc = P.find_dxc()
    manifest = json.loads((ROOT / "shaders" / "manifest.json").read_text())
    index = list(csv.DictReader(open(ROOT / "extracted" / "index.csv")))
    if len(sys.argv) > 1:
        targets = [(sys.argv[1], sys.argv[2:] or "all",
                    next((t.get("args", {}) for t in manifest["patches"] if t["entry"] == sys.argv[1]), {}))]
    else:
        targets = [(t["entry"], t.get("crcs", "all"), t.get("args", {})) for t in manifest["patches"]
                   if t.get("kind", "medium") == "medium" and not t.get("lab_only")]
    rng = np.random.default_rng(7)
    failed = 0
    for entry, crcs, args in targets:
        for row in index:
            crc = Path(row["path"]).name.split(".")[0]
            if row["entry"] != entry or (crcs != "all" and crc not in crcs):
                continue
            problems = check(entry, crc, P.disassemble(ROOT / "extracted" / row["path"], dxc), rng, args)
            for p in problems[:12]:
                print("   ", p)
            failed += bool(problems)
    if failed:
        raise SystemExit(f"{failed} shader(s) failed")


if __name__ == "__main__":
    main()

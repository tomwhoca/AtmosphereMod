"""Check the ch_cloudDensity capture patch (patch_dxil.patch_cloud_gen_capture).

Runs the stock and the patched live-weather variant (0x736e10e6), and two of
the layer family (presets, Active Sky: 0x9197d2d6, and 0x41442a54 with its 2D
map), thread by thread in a small DXIL interpreter, with made-up textures and
constants:
- the volume it stores is identical to stock, with the capture off and on;
- the header, cbuffers, coverage curve, weather maps, precipitation maps,
  noise and both output copies land in the debug buffer where the add-on and
  tools/cloud_capture_view.py read them, byte for byte;
- METAR anchoring (live-weather family, C20, C21 and the add-on's station
  table in the debug buffer): off (C20 = 0, no bit 6, no table), the volume
  is stock; on, every slice's weather-map value is the float64 blend toward
  three stations' rows, by distance (a planet frame from a capture near
  EKCH, a station at EKCH, one fading in). Where the developer's generator
  model is present, the volume is also checked against it.

    python tools/test_cloud_gen_capture.py
"""
import csv
import math
import re
import struct
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "lab" / "cloud_density"))
try:
    import model as generator_model  # noqa: E402
except ImportError:  # (optional; skipped when absent)
    generator_model = None
import patch_dxil as P  # noqa: E402

CRC = "0x736e10e6"
N = 512
STAMP = 0x1234
V = r"%[\w.]+"


def f32(x):
    return float(np.float32(x))


def cst(tok):
    if tok.startswith("0x"):
        return f32(struct.unpack(">d", bytes.fromhex(tok[2:]))[0])
    if tok in ("true", "false"):
        return tok == "true"
    if tok == "undef":
        return 0
    return float(tok) if ("." in tok or "e" in tok) else int(tok)


class Prog:
    def __init__(self, text):
        self.blocks, cur, body, started = {}, "%0", [], False
        for line in text.splitlines():
            if line.startswith("define void @"):
                started = True
                continue
            if not started:
                continue
            if line.startswith("}"):
                break
            m = re.match(r"^; <label>:(\d+)", line) or re.match(r"^([\w.]+):", line)
            if m:
                self.blocks[cur] = body
                cur, body = "%" + m[1], []
                continue
            s = re.sub(r"\s+;.*$", "", line.strip())
            if s and not s.startswith(";"):
                body.append(s)
        self.blocks[cur] = body


UN = {12: math.cos, 13: math.sin, 25: lambda x: 1 / math.sqrt(x) if x > 0 else math.inf,
      7: lambda x: min(max(x, 0.0), 1.0), 24: lambda x: math.sqrt(x) if x >= 0 else math.nan,
      23: lambda x: math.log2(x) if x > 0 else (-math.inf if x == 0 else math.nan),
      21: lambda x: 2.0 ** x if x < 128 else math.inf, 22: lambda x: x - math.floor(x), 6: abs,
      27: math.floor, 28: math.ceil, 29: math.trunc, 16: math.asin, 17: math.atan}
M32 = 0xFFFFFFFF


class Machine:
    """One thread. cb: {(space, reg): array[n, 4]}; tex: {reg: object with sample(*c, lod) and load(x, y, z)
    and dims}; buf: the debug buffer (bytearray)."""

    def __init__(self, prog, cb, tex, buf):
        self.prog, self.cb, self.tex, self.buf = prog, cb, tex, buf

    def run(self, tid):
        env, stores, prev, blk = {}, {}, None, "%0"
        self.env = env

        def v(tok):
            return env[tok] if tok.startswith("%") else cst(tok)

        while True:
            body = self.prog.blocks[blk]
            new = {}
            for s in body:
                m = re.match(rf"({V}) = phi \w+ (.*)$", s)
                if m:
                    arms = dict((b, val) for val, b in re.findall(rf"\[ (\S+), ({V}) \]", m[2]))
                    new[m[1]] = v(arms[prev])
            env.update(new)
            nxt = None
            for s in body:
                if " = phi " in s:
                    continue
                m = re.match(rf"({V}) = (.*)$", s)
                if m:
                    env[m[1]] = self.ev(m[2], v, tid)
                    continue
                if s.startswith("br "):
                    mm = re.match(rf"br i1 (\S+), label ({V}), label ({V})", s)
                    nxt = (mm[2] if v(mm[1]) else mm[3]) if mm else re.match(rf"br label ({V})", s)[1]
                    break
                if s.startswith("ret"):
                    return stores
                mm = re.match(r"call void @dx\.op\.textureStore\.f32\(i32 67, %dx\.types\.Handle \S+, i32 \S+, "
                              r"i32 \S+, i32 (\S+), float (\S+), float (\S+), float (\S+), float (\S+), i8 15\)", s)
                if mm:
                    stores[int(v(mm[1]))] = tuple(f32(v(mm[i])) for i in range(2, 6))
                    continue
                mm = re.match(r"call void @dx\.op\.rawBufferStore\.(i32|f32)\(i32 140, %dx\.types\.Handle \S+, "
                              r"i32 (\S+), i32 undef, \w+ (\S+), \w+ (\S+), \w+ (\S+), \w+ (\S+), i8 (\d+), i32 4\)", s)
                if mm:
                    off, mask = int(v(mm[2])), int(mm[7])
                    for k in range(4):
                        if mask >> k & 1:
                            x = v(mm[3 + k])
                            raw = struct.pack("<I", int(x) & M32) if mm[1] == "i32" else struct.pack("<f", x)
                            self.buf[off + 4 * k:off + 4 * k + 4] = raw
                    continue
                raise RuntimeError("unhandled: " + s)
            prev, blk = blk, nxt

    def ev(self, rhs, v, tid):
        m = re.match(r"call %dx\.types\.Handle @dx\.op\.createHandleFromBinding\(i32 217, %dx\.types\.ResBind "
                     r"(zeroinitializer|\{ i32 (\d+), i32 \d+, i32 (\d+), i8 (\d+) \})", rhs)
        if m:
            return (0, 0, 0) if m[1] == "zeroinitializer" else (int(m[4]), int(m[2]), int(m[3]))
        m = re.match(rf"call %dx\.types\.Handle @dx\.op\.annotateHandle\(i32 216, %dx\.types\.Handle ({V}),", rhs)
        if m:
            return v(m[1])
        m = re.match(r"call i32 @dx\.op\.threadId\.i32\(i32 93, i32 (\d)\)", rhs)
        if m:
            return tid[int(m[1])]
        m = re.match(rf"call %dx\.types\.CBufRet\.f32 @dx\.op\.cbufferLoadLegacy\.f32\(i32 59, %dx\.types\.Handle "
                     rf"({V}), i32 (\S+)\)", rhs)
        if m:
            cls, reg, space = v(m[1])
            return tuple(f32(x) for x in self.cb[(space, reg)][int(v(m[2]))])
        m = re.match(rf"call %dx\.types\.CBufRet\.i32 @dx\.op\.cbufferLoadLegacy\.i32\(i32 59, %dx\.types\.Handle "
                     rf"({V}), i32 (\S+)\)", rhs)
        if m:
            cls, reg, space = v(m[1])
            row = self.cb[(space, reg)][int(v(m[2]))]
            return tuple(struct.unpack("<i", struct.pack("<f", float(x)))[0] for x in row)
        m = re.match(rf"call %dx\.types\.ResRet\.f32 @dx\.op\.rawBufferLoad\.f32\(i32 139, %dx\.types\.Handle "
                     rf"{V}, i32 (\S+), i32 undef, i8 15, i32 4\)", rhs)
        if m:
            return struct.unpack_from("<4f", self.buf, int(v(m[1]))) + (0,)
        m = re.match(rf"extractvalue %dx\.types\.\w+(?:\.[fi]32)? ({V}), (\d)", rhs)
        if m:
            return v(m[1])[int(m[2])]
        m = re.match(rf"call %dx\.types\.Dimensions @dx\.op\.getDimensions\(i32 72, %dx\.types\.Handle ({V}), i32 0\)",
                     rhs)
        if m:
            return self.tex[v(m[1])[1]].dims
        m = re.match(rf"call %dx\.types\.ResRet\.f32 @dx\.op\.textureLoad\.f32\(i32 66, %dx\.types\.Handle ({V}), "
                     r"i32 0, i32 (\S+), i32 (\S+), i32 (\S+),", rhs)
        if m:
            return tuple(self.tex[v(m[1])[1]].load(int(v(m[2])), int(v(m[3])), int(v(m[4])))) + (0,)
        m = re.match(rf"call %dx\.types\.ResRet\.f32 @dx\.op\.sampleLevel\.f32\(i32 62, %dx\.types\.Handle ({V}), "
                     r"%dx\.types\.Handle \S+, float (\S+), float (\S+), float (\S+), float \S+, i32 \S+, i32 \S+, "
                     r"i32 \S+, float (\S+)\)", rhs)
        if m:
            coords = [v(c) for c in (m[2], m[3], m[4]) if c != "undef"]
            return tuple(f32(x) for x in self.tex[v(m[1])[1]].sample(*coords, lod=v(m[5]))) + (0,)
        m = re.match(r"f(add|sub|mul|div) (?:fast )?float (\S+), (\S+)", rhs)
        if m:
            a, b = v(m[2]), v(m[3])
            if m[1] == "div":
                return a / b if b != 0 else (math.copysign(math.inf, a) if a else math.nan)
            return f32({"add": a + b, "sub": a - b, "mul": a * b}[m[1]])
        m = re.match(r"fcmp (?:fast )?(\w+) float (\S+), (\S+)", rhs)
        if m:
            a, b = v(m[2]), v(m[3])
            uno = math.isnan(a) or math.isnan(b)
            r = {"gt": a > b, "lt": a < b, "ge": a >= b, "le": a <= b, "eq": a == b, "ne": a != b}[m[1][1:]]
            return (r or uno) if m[1][0] == "u" else (r and not uno)
        m = re.match(r"icmp (\w+) i32 (\S+), (\S+)", rhs)
        if m:
            a, b = int(v(m[2])) & M32, int(v(m[3])) & M32
            sa, sb = (a ^ 0x80000000) - 0x80000000, (b ^ 0x80000000) - 0x80000000
            return {"eq": a == b, "ne": a != b, "ult": a < b, "ugt": a > b, "ule": a <= b, "uge": a >= b,
                    "slt": sa < sb, "sgt": sa > sb, "sle": sa <= sb, "sge": sa >= sb}[m[1]]
        m = re.match(r"select i1 (\S+), \w+ (\S+), \w+ (\S+)", rhs)
        if m:
            return v(m[2]) if v(m[1]) else v(m[3])
        m = re.match(r"call float @dx\.op\.unary\.f32\(i32 (\d+), float (\S+)\)", rhs)
        if m:
            x = v(m[2])
            return 0.0 if (m[1] == "7" and math.isnan(x)) else f32(UN[int(m[1])](x))
        m = re.match(r"call float @dx\.op\.binary\.f32\(i32 (\d+), float (\S+), float (\S+)\)", rhs)
        if m:
            return (max if m[1] == "35" else min)(v(m[2]), v(m[3]))
        m = re.match(r"call float @dx\.op\.tertiary\.f32\(i32 46, float (\S+), float (\S+), float (\S+)\)", rhs)
        if m:
            return f32(v(m[1]) * v(m[2]) + v(m[3]))
        m = re.match(r"call float @dx\.op\.dot2\.f32\(i32 54, float (\S+), float (\S+), float (\S+), float (\S+)\)",
                     rhs)
        if m:
            return f32(v(m[1]) * v(m[3]) + v(m[2]) * v(m[4]))
        m = re.match(r"call float @dx\.op\.dot([34])\.f32\(i32 5[56], (.*)\)$", rhs)
        if m:
            args = [v(a.split()[-1]) for a in m[2].split(", ")]
            n = int(m[1])
            return f32(sum(args[i] * args[n + i] for i in range(n)))
        m = re.match(r"bitcast float (\S+) to i32", rhs)
        if m:
            return struct.unpack("<i", struct.pack("<f", v(m[1])))[0]
        m = re.match(r"(?:uitofp|sitofp) i32 (\S+) to float", rhs)
        if m:
            return float(v(m[1]))
        m = re.match(r"fptoui float (\S+) to i32", rhs)
        if m:
            return int(v(m[1])) & M32
        m = re.match(r"fptosi float (\S+) to i32", rhs)
        if m:
            return int(v(m[1])) & M32  # (two's complement)
        m = re.match(r"(add|sub|mul|and|or|xor|shl|lshr|udiv|urem)(?: nuw)?(?: nsw)? i32 (\S+), (\S+)", rhs)
        if m:
            a, b = int(v(m[2])) & M32, int(v(m[3])) & M32
            return {"add": a + b, "sub": a - b, "mul": a * b, "and": a & b, "or": a | b, "xor": a ^ b,
                    "shl": a << b, "lshr": a >> b, "udiv": a // max(b, 1), "urem": a % max(b, 1)}[m[1]] & M32
        m = re.match(r"(and|or|xor) i1 (\S+), (\S+)", rhs)
        if m:
            a, b = bool(v(m[2])), bool(v(m[3]))
            return {"and": a and b, "or": a or b, "xor": a != b}[m[1]]
        raise RuntimeError("unhandled rhs: " + rhs)


def h(*k):
    """A deterministic pseudo-random value in [0, 1)."""
    x = 0x9E3779B9
    for q in k:
        x = ((x ^ (int(q) & M32)) * 0x85EBCA6B) & M32
        x ^= x >> 13
    return (x & 0xFFFF) / 65536.0


class Tex:
    def __init__(self, reg, dims, levels=256):
        self.reg, self.dims, self.levels = reg, dims, levels

    def load(self, x, y, z):
        w, hh = self.dims[0], self.dims[1]
        d = self.dims[2] if self.dims[2] else 1
        if not (0 <= x < w and 0 <= y < hh and 0 <= z < d):
            return (0.0, 0.0, 0.0, 0.0)
        return tuple(f32(round(h(self.reg, x, y, z, c) * (self.levels - 1)) / (self.levels - 1)) for c in range(4))

    def sample(self, *c, lod=0.0):
        # smooth and in range: what the stock maths gets doesn't matter, only that stock and patched agree
        s = sum(c)
        return tuple(f32(0.5 + 0.45 * math.sin(3.1 * s + 1.7 * k + self.reg)) for k in range(4))


def constants(flags, disc=2.0, cloud=None):
    G = np.zeros((300, 4))
    U = np.zeros((658, 4))
    Pc = np.zeros((52, 4))
    U[1] = (1 / N, 1 / N, 0, disc)
    Pc[4] = (0, 0, 307200, 0)
    G[110] = (1, 0, 0, 0)
    G[111] = (0, 1, 0, 0)
    G[112] = (0, 0, 1, 0)
    G[92] = (0, 0, 1 / 150000, 0)
    Pc[0] = (1 / 307200 / 2, 0, 0, 0.5)
    Pc[2] = (0, 0, 1 / 307200 / 2, 0.5)
    Pc[6] = (0.3, 0.6, 0, 0)
    U[2] = (0, 0, 1 / 20000, 1 / 20000)
    for r in range(64):  # recognisable register contents beyond the ones the maths reads
        if r not in (1, 2, 6) and r < 10:
            U[r] = (r + 0.25, r + 0.5, r + 0.75, -r)
    for r in range(52):
        if r not in (0, 2, 4, 6):
            Pc[r] = (100 + r, 0.5 * r, -r, 7)
    mod = np.zeros((4, 4))
    mod[0, 0] = struct.unpack("<f", struct.pack("<I", flags))[0]
    mod[0, 1] = struct.unpack("<f", struct.pack("<I", STAMP))[0]
    clouds = np.zeros((P.CLOUD_PARAMS_SIZE // 16, 4))
    if cloud is not None:
        clouds.flat[:len(cloud)] = cloud
        # a planet frame from a capture near EKCH (G110..G112)
        G[110] = (1.308447e-07, 8.637934e-08, -5.639204e-12, 0.5522037)
        G[111] = (-1.279492e-08, 1.937121e-08, -1.550573e-07, 0.1238358)
        G[112] = (-8.600219e-08, 1.302749e-07, 2.337183e-08, 0.8272431)
        G[117] = (0, 8.790774, 0, 0)
    return {(0, 1): G, (0, 0): U, (0, 2): Pc, (50, 0): mod, (50, 1): clouds}


def bits(u):
    return struct.unpack("<f", struct.pack("<I", u))[0]


# three stations: (latitude, longitude (rad), weight, layers (base m MSL, top m
# MSL, C, kind: 0 domed, 1 flat deck, 3 smooth deck), what the METAR describes
# (m MSL)). EKCH; one placed on column (300, 220) with a low overcast; one
# fading in on column (200, 330). Slots 3-7 unused.
EKCH = (math.radians(55.6180), math.radians(12.6560))
STATIONS = [
    [EKCH, 1.0, [(158.0, 658.0, 0.58, 3), (1000.0, 2200.0, 0.38, 0), (9000.0, 9800.0, 0.21, 1)], 3700.0],
    [None, 1.0, [(130.0, 580.0, 1.0, 3)], 580.0],
    [None, 0.4, [(1200.0, 1800.0, 0.58, 1)], 3700.0],
]
STATION_TIDS = {1: (300, 220), 2: (200, 330)}
NOW = 2500.0  # (the storm cells' check: Global[0].y)
STRENGTH, RADIUS_KM = 0.8, 20.0


class LutTex(Tex):
    """A coverage curve low enough that the anchored layers make cloud from the
    mock noise, so the runs, and with them the dome taper, are exercised."""

    def sample(self, *c, lod=0.0):
        v = f32(0.9 * (1.0 - c[0]))
        return (v, v, v, v)


class ProfileTex(Tex):
    """A weather map with one value per slice everywhere."""

    def __init__(self, reg, dims, profile):
        super().__init__(reg, dims)
        self.profile = profile

    def sample(self, *c, lod=0.0):
        k = int(c[2] * 32)
        v = f32(self.profile[k]) if 0 <= k < len(self.profile) else 0.0
        return (v, v, v, v)


class FlatTex(Tex):
    """The same value everywhere (terrain)."""

    def __init__(self, reg, dims, value):
        super().__init__(reg, dims)
        self.value = f32(value)

    def sample(self, *c, lod=0.0):
        return (self.value,) * 4


def anchor_cloud(strength=STRENGTH):
    c = [0.0] * (P.CLOUD_PARAMS_SIZE // 4)
    c[20], c[21] = strength, RADIUS_KM
    return c


def anchor_rows(layers, ceiling, storm=0.0):
    """A station's 32 rows (C, a, deck, smooth), as the add-on builds them (cloud_anchor.h rows); the
    last is the storm cells' evidence (storm, 1, 0, 0) (cloud_anchor.h kStormRow)."""
    def inside(b, t, k):
        if not 0 <= k < P.WEATHER_SLICES:
            return False
        lo, hi = 15.625 * k * k, 15.625 * (k + 1) ** 2
        return max(0.0, min(t, hi) - max(b, lo)) / min(max(t - b, 1.0), hi - lo) >= 0.5

    rows = []
    for k in range(P.ANCHOR_ROWS):
        centre = 15.625 * (k + 0.5) ** 2
        best = st = sm = 0.0
        covered = False
        for b, t, cv, kind in layers:
            if inside(b, t, k):
                covered = True
                top = kind in (1, 3) and not inside(b, t, k + 1) and inside(b, t, k - 1)  # a deck's top slice
                best = max(best, min(cv, P.DECK_TOP_C) if top else cv)
                st = max(st, 1.0 if kind in (1, 3) else 0.0)
                sm = max(sm, 1.0 if kind == 3 and not top else 0.0)
        a = 1.0 if covered or (centre < ceiling and k < P.WEATHER_SLICES) else 0.0
        rows.append((best, a, st, sm) if k < P.ANCHOR_ROWS - 1 else (storm, 1.0, 0.0, 0.0))
    return rows


def unit(lat, lon):
    return np.array([math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)])


def with_table(stations, magic=P.ANCHOR_MAGIC):
    """A debug buffer holding the station table at ANCHOR_TABLE."""
    buf = bytearray(16 << 20)
    o = P.ANCHOR_TABLE
    struct.pack_into("<4I", buf, o, magic, len(stations), P.ANCHOR_STATIONS, 0)
    for i, ((lat, lon), wt, layers, ceiling, *storm) in enumerate(stations):
        struct.pack_into("<4f", buf, o + 16 + 16 * i, *unit(lat, lon), wt)
        for k, row in enumerate(anchor_rows(layers, ceiling, *storm)):
            struct.pack_into("<4f", buf, o + 16 + 16 * P.ANCHOR_STATIONS + 16 * (P.ANCHOR_ROWS * i + k), *row)
    return buf


def anchor_ref(stock_c, direction, stations, strength=STRENGTH):
    """The anchored weather values, float64, from the stock values and the column's planet direction."""
    d = np.array(direction, dtype=np.float64)
    d /= np.linalg.norm(d)
    r = max(RADIUS_KM * 1000, P.ANCHOR_MIN_RADIUS_M)
    g, b, dist, rows = [], [], [], []
    for (lat, lon), wt, layers, ceiling in stations:
        di = np.linalg.norm(d - unit(lat, lon)) * P.EARTH_RADIUS_M
        gi = strength * wt * math.exp(-(max(0.0, di - r) / r) ** 2)
        g.append(gi)
        b.append(gi / ((di / 1000) ** 2 + P.ANCHOR_IDW_KM ** 2))
        dist.append(di)
        rows.append(anchor_rows(layers, ceiling))
    out, strat, smooth = [], [], []
    for k in range(P.WEATHER_SLICES):
        q, sw, sc, sst, ssm = 1.0, 0.0, 0.0, 0.0, 0.0
        for i in range(len(stations)):
            v, a, st, sm = rows[i][k]
            q *= 1.0 - g[i] * a
            wb = b[i] * a
            sw, sc, sst, ssm = sw + wb, sc + wb * v, sst + wb * st, ssm + wb * sm
        w, inv = 1.0 - q, 1.0 / max(sw, 1e-30)
        c = stock_c[k]
        out.append(c + (sc * inv - c) * w)
        strat.append(sst * inv * w * P.DECK_FLATTEN)
        smooth.append(ssm * inv * w * P.DECK_SMOOTH)
    total = 1.0 - math.prod(1.0 - gi for gi in g)
    nearest = min(di for di, st in zip(dist, stations) if st[1] > 0)
    return out, nearest, total, strat, smooth, dist



TEX = {0: Tex(0, (64, 64, 0, 1)), 1: Tex(1, (512, 512, 0, 1)), 2: Tex(2, (256, 128, 0, 1), levels=1 << 16),
       3: Tex(3, (256, 128, 0, 1), levels=1 << 16), 4: Tex(4, (512, 512, 32, 10)), 5: Tex(5, (256, 256, 32, 1)),
       6: Tex(6, (256, 256, 32, 1)), 7: Tex(7, (101, 1, 0, 1), levels=1 << 16)}


def u8(x):
    return int(math.floor(min(max(x, 0.0), 1.0) * 255 + 0.5))


# the layer family: t2 3D noise, t3 coverage curve, t4 (some variants) a 2D map
TEX_LAYERS = {0: Tex(0, (64, 64, 0, 1)), 1: Tex(1, (512, 512, 0, 1)), 2: Tex(2, (128, 128, 64, 8)),
              3: Tex(3, (101, 1, 0, 1), levels=1 << 16), 4: Tex(4, (256, 256, 0, 1), levels=1 << 16)}
LAYER_CRCS = {"0x9197d2d6": False, "0x41442a54": True}  # crc -> binds the 2D map t4


def smoothstep(x, lo, hi):
    t = min(max((x - lo) / (hi - lo), 0.0), 1.0)
    return t * t * (3 - 2 * t)


def types_ref(c, terrain, ice=P.OPTICAL_ICE_DEFAULT_M):
    """The 32 slices' type weights (Sc, St, Ac, As), float64, from the column's
    weather values c (31) and terrain (m), as patch_dxil.cloud_types_lines."""
    n = P.WEATHER_SLICES
    inside = [x > P.TYPES_IN_LAYER_C for x in c]
    base, cover_up = [], []
    for k in range(n):
        joined = k > 0 and inside[k - 1]
        base.append(base[k - 1] if joined else 15.625 * k * k)
        cover_up.append(max(c[k], cover_up[k - 1] if joined else 0.0))
    top, cover = [0.0] * n, [0.0] * n
    for k in reversed(range(n)):
        joined = k + 1 < n and inside[k + 1]
        top[k] = top[k + 1] if joined else 15.625 * (k + 1) ** 2
        cover[k] = max(cover_up[k], cover[k + 1] if joined else 0.0)
    weights = []
    for k in range(n):
        depth, above = top[k] - base[k], base[k] - terrain
        broken = smoothstep(cover[k], *P.TYPES_BROKEN)
        low = 1 - smoothstep(above, *P.TYPES_LOW_BASE)
        mid = (1 - low) * (1 - smoothstep(base[k], ice - P.TYPES_ICE_BLEND_M, ice))
        st = low * (1 - smoothstep(above, *P.TYPES_ST_BASE)) * (1 - smoothstep(depth, *P.TYPES_ST_DEPTH))
        sc = broken * (low * (1 - smoothstep(depth, *P.TYPES_SC_DEPTH)) - st)
        mid_nd = mid * (1 - smoothstep(depth, *P.TYPES_DEEP))
        as_ = mid_nd * broken * smoothstep(depth, *P.TYPES_AS_DEPTH)
        weights.append((sc, st, mid_nd - as_, as_) if inside[k] else (0.0,) * 4)
    out = []
    for k in range(P.TYPES_SLICES):
        if k < n and inside[k]:
            out.append(weights[k])
        elif 1 <= k <= n and inside[k - 1]:
            out.append(weights[k - 1])
        else:
            out.append(weights[k + 1] if k + 1 < n else (0.0,) * 4)
    return out


def check_types_table(check):
    """The classification itself, on made-up columns (sea-level terrain): the
    approved table (notes/cloud-shapes.md 4h)."""
    def column(c, slices):
        v = [0.0] * P.WEATHER_SLICES
        for k in slices:
            v[k] = c
        return v
    names = ("Sc", "St", "Ac", "As")
    cases = [("OVC stratus 140-560 m", column(1.0, range(3, 6)), "St"),
             ("BKN stratocumulus 1-1.6 km", column(0.58, range(8, 10)), "Sc"),
             ("SCT cumulus 1-1.9 km", column(0.38, range(8, 11)), "Cu"),
             ("FEW stratus fractus 400-770 m", column(0.21, range(5, 7)), "St"),
             ("SCT stratus fractus 250-560 m", column(0.38, range(4, 6)), "St"),
             ("FEW cumulus 770-1410 m", column(0.21, range(7, 9)), "Cu"),
             ("BKN towering cumulus 1-6 km", column(0.58, range(8, 19)), "Cu"),
             ("BKN altostratus 3.5-4.5 km", column(0.6, range(15, 17)), "As"),
             ("SCT altocumulus 3.5-4.5 km", column(0.38, range(15, 17)), "Ac"),
             ("BKN cirrus 9.8-11.3 km", column(0.6, range(25, 27)), "Cu")]
    for what, c, want in cases:
        w = types_ref(c, 0.0)
        k = next(k for k in range(P.WEATHER_SLICES) if c[k] > 0)
        rest = 1 - sum(w[k])
        got = max(zip((*w[k], rest), (*names, "Cu")))[1]
        check(got == want and max(*w[k], rest) > 0.7, f"cloud types: {what} is {got} ({w[k]}, cumulus {rest:.2f})")
    print("cloud types: the classification table holds on", len(cases), "made-up layers")


def check_types(stock_p, patched_p, values, h_name, table, tex, check):
    """The live-weather family's grid: the first thread of each cell writes
    its column's weights (against types_ref), the centre thread the header;
    other threads and runs without bit 7 write nothing, and the volume is
    unchanged."""
    flags = P.DEBUG_BUFFER_16MB | P.DEBUG_BUFFER_TYPES | (5 << 16)
    big = bytearray(table) + bytearray((20 << 20) - len(table))
    worst = 0
    for tid in [(256, 256), (300, 220), (200, 328), (128, 400), (40, 40), (264, 248), (4, 508)]:
        ref = Machine(patched_p, constants(P.DEBUG_BUFFER_16MB, cloud=anchor_cloud()), tex, bytearray(big)).run(tid)
        m = Machine(patched_p, constants(flags, cloud=anchor_cloud()), tex, bytearray(big))
        out = m.run(tid)
        check(out == ref, f"{tid}: cloud types changed the volume")
        c = [m.env[values[k]] for k in range(P.WEATHER_SLICES)]
        want = types_ref(c, m.env[h_name])
        gx, gy = tid[0] * P.TYPES_N // N, tid[1] * P.TYPES_N // N
        for k in range(P.TYPES_SLICES):
            o = P.TYPES_REGION + 16 + 4 * ((k * P.TYPES_N + gy) * P.TYPES_N + gx)
            got = list(m.buf[o:o + 4])
            exp = [u8(x) for x in want[k]]
            err = max(abs(a - b) for a, b in zip(got, exp))
            worst = max(worst, err)
            check(err <= 1, f"{tid} slice {k}: cloud type weights {got} != {exp}")
        hdr = struct.unpack_from("<3If", m.buf, P.TYPES_REGION)
        if tid == (N // 2, N // 2):
            check(hdr == (P.TYPES_MAGIC, 5, N, 64.0 / N), f"cloud types header {hdr}")
        else:
            check(hdr == (0, 0, 0, 0.0), f"{tid}: wrote the cloud types header")
        kinds = [max(zip((*w, 1 - sum(w)), ("Sc", "St", "Ac", "As", "Cu")))[1] for w, x in zip(want, c)
                 if x > P.TYPES_IN_LAYER_C]
        print(f"cloud types at {tid}: cell ({gx}, {gy}), {len(kinds)} slices in layers ({' '.join(kinds)}), "
              f"max byte error {worst}")
    # layered weather maps (no anchoring, terrain at 50 m): each layer's type end to end
    cases = [("OVC stratus", [((3, 6), 1.0)], ["St"]),
             ("BKN stratocumulus", [((8, 10), 0.58)], ["Sc"]),
             ("SCT cumulus", [((8, 11), 0.38)], ["Cu"]),
             ("BKN stratocumulus under BKN altostratus", [((8, 10), 0.58), ((15, 17), 0.6)], ["Sc", "As"]),
             ("SCT altocumulus under cirrus", [((15, 17), 0.38), ((25, 27), 0.6)], ["Ac", "Cu"]),
             ("BKN towering cumulus", [((8, 19), 0.58)], ["Cu"])]
    for what, layers, kinds in cases:
        profile = [0.0] * P.WEATHER_SLICES
        for (k0, k1), cv in layers:
            profile[k0:k1] = [cv] * (k1 - k0)
        tex = {**TEX, 0: FlatTex(0, (64, 64, 0, 1), 50.0 / 65535), 5: ProfileTex(5, TEX[5].dims, profile),
               6: ProfileTex(6, TEX[6].dims, profile)}
        m = Machine(patched_p, constants(flags, cloud=[0.0] * 32), tex, bytearray(20 << 20))
        m.run((N // 2, N // 2))
        c = [m.env[values[k]] for k in range(P.WEATHER_SLICES)]
        want = types_ref(c, m.env[h_name])
        g = P.TYPES_N // 2
        got_kinds = []
        for k in range(P.TYPES_SLICES):
            o = P.TYPES_REGION + 16 + 4 * ((k * P.TYPES_N + g) * P.TYPES_N + g)
            got = list(m.buf[o:o + 4])
            check(max(abs(a - u8(b)) for a, b in zip(got, want[k])) <= 1,
                  f"{what} slice {k}: cloud type weights {got} != {[u8(x) for x in want[k]]}")
            if any(k == k0 for (k0, _), _ in layers):
                w = [x / 255 for x in got]
                got_kinds.append(max(zip((*w, 1 - sum(w)), ("Sc", "St", "Ac", "As", "Cu")))[1])
        check(got_kinds == kinds, f"{what}: the shader's types {got_kinds}, expected {kinds}")
        print(f"cloud types, {what}: {got_kinds}")
    # not the first thread of its cell; no bit 7: nothing in the grid
    for tid, f in (((257, 256), flags), ((256, 256), P.DEBUG_BUFFER_16MB)):
        m = Machine(patched_p, constants(f, cloud=anchor_cloud()), tex, bytearray(big))
        m.run(tid)
        check(not any(m.buf[P.TYPES_REGION:]), f"{tid} flags {f:#x}: wrote to the cloud types grid")


def check_specks(stock_p, patched_p, values, noise, t1_name, h_name, lut, tex, check):
    """Speck suppression (patch_dxil.apply_speck_suppression): a weather map
    with clear slices, sub-okta ones and a layer, the low coverage curve (so
    the mock noise grazes the threshold in clear slices). With C16 off the
    volume is stock; with it on, slices below SPECK_C[0] hold no coverage
    (x <= 0.49) and the whole column matches the model with the lift."""
    profile = [0.0] * P.WEATHER_SLICES
    profile[7], profile[12], profile[16] = 0.1, 0.2, 0.06
    for k in (9, 10, 11):
        profile[k] = 0.5
    dims = tex[5].dims
    tex_s = {**tex, 5: ProfileTex(5, dims, profile), 6: ProfileTex(6, dims, profile)}
    on = [0.0] * (P.CLOUD_PARAMS_SIZE // 4)
    on[0], on[16], on[17] = 1.0, 2.0, 0.33
    off = list(on)
    off[16] = 0.0
    lift = [P.SPECK_LIFT * (1 - smoothstep(c, *P.SPECK_C)) for c in profile]
    stock_specks = removed = 0
    worst = 0.0
    for tid in [(256, 256), (250, 262), (230, 270), (300, 220), (128, 400), (40, 300), (400, 128), (180, 190),
                (330, 310), (270, 130)]:
        ref_m = Machine(stock_p, constants(0, cloud=off), tex_s, bytearray(16 << 20))
        ref = ref_m.run(tid)
        got_off = Machine(patched_p, constants(0, cloud=off), tex_s, bytearray(16 << 20)).run(tid)
        check(got_off == ref, f"{tid}: specks: C16 = 0 but the volume differs from stock")
        m = Machine(patched_p, constants(0, cloud=on), tex_s, bytearray(16 << 20))
        out = m.run(tid)
        c = [m.env[values[k]] for k in range(P.WEATHER_SLICES)]
        check(all(abs(a - b) < 1e-6 for a, b in zip(c, profile)), f"{tid}: specks: weather values {c}")
        for k in range(P.WEATHER_SLICES):
            if profile[k] <= P.SPECK_C[0]:
                stock_specks += ref[k][0] > 0.49
                removed += ref[k][0] > 0.49 and out[k][0] <= 0.49
                check(out[k][0] <= 0.49, f"{tid} slice {k}: a speck survived (x {out[k][0]:.3f}, C {profile[k]})")
        if generator_model is not None:
            n4 = [tuple(ref_m.env[noise[k]][:4]) for k in range(P.WEATHER_SLICES)]
            res = generator_model.column(profile, n4, lut, ref_m.env[t1_name], ref_m.env[h_name], 0.0, lift=lift)
            err = max(max(abs(out[k][0] - res[k][0]), abs(out[k][1] - res[k][1])) for k in range(P.WEATHER_SLICES))
            worst = max(worst, err)
            check(err < 1e-4, f"{tid}: specks: the volume (x, y) differs from the model by {err:.1e}")
    check(stock_specks > 0, "specks: the mock weather made no specks to remove")
    print(f"specks: {stock_specks} stock speck slices below C {P.SPECK_C[0]} in 10 columns, {removed} removed; "
          f"volume vs model {worst:.1e}" + ("" if generator_model is not None else " (no model)"))


class RainTex(Tex):
    """A rain map (mm/h) with convective areas and gaps a few tens of km across."""

    def sample(self, *c, lod=0.0):
        u, v = c[0], c[1]
        r = f32(max(0.0, 40.0 + 45.0 * math.sin(40.0 * u + 1.3 + self.reg) * math.cos(37.0 * v + 0.4)))
        return (r, r, r, r)


def mix32(a):
    """patch_dxil.apply_cb_towers' integer hash finish (wrapping 32-bit)."""
    a ^= a >> 15
    a = (a * P.CB_HASH[2]) & M32
    a ^= a >> 12
    a = (a * P.CB_HASH[3]) & M32
    return a ^ (a >> 15)


def towers_ref(p, uv, rows, du, dv, lerp, terrain, rain, wind, msfs=None, now=0.0, stations=()):
    """The storm cells' (x, y) per slice, the nearest-to-threshold margin, the number of cells and
    the stratiform shares, float64, from the column's planet coordinates p, map coordinates uv,
    the planet rows' X and Z coefficients rows[i] = (cx, cz), the map's per-metre coefficients du,
    dv, the rain blend, the terrain, the wind (P[5].xy), MSFS's own shape per slice, msfs =
    (s_k after the specks, N_k) (None: no MSFS cloud, N 0.5), the time (Global[0].y) and the stations'
    storm evidence [(unit vector, weight x evidence)]; rain(u, v) samples the two keyframes. Also the
    anvils' (level, cover) and the towers' (mark, base) for the renderer."""
    s_fin, noise = msfs if msfs is not None else ([-1.0] * P.WEATHER_SLICES, [0.5] * P.WEATHER_SLICES)
    n = math.sqrt(sum(c * c for c in p))
    u = [c / n for c in p]
    lat = math.degrees(math.asin(max(-1.0, min(1.0, u[2]))))
    lon = math.degrees(math.atan2(p[1], p[0]))
    trop = P.tropopause_m(lat)
    # the base: MSFS's lowest cloud in the column within range of the terrain
    base = terrain + P.CB_BASE_AGL_M
    for k in reversed(range(P.WEATHER_SLICES)):
        if s_fin[k] > 0 and P.slice_bottom(k) - terrain < P.CB_BASE_RANGE_M:
            base = P.slice_bottom(k)
    base = max(base, terrain + P.CB_BASE_MIN_M)
    v = math.hypot(*wind)
    wd = (1.0, 0.0) if v * v < 1e-4 else (wind[0] / v, wind[1] / v)
    s = min(v / P.CB_WIND_FULL, 1.0)
    dlat = P.CB_CELL_KM / P.EARTH_KM_PER_DEG
    ri = lat / dlat
    i0 = math.floor(ri)
    margin = min(ri - i0, 1 - (ri - i0))
    towers, anvils, marks = [], [], []
    for row in (i0 - 1, i0, i0 + 1):
        dlon = dlat / max(math.cos(math.radians((row + 0.5) * dlat)), 0.1)
        rj = lon / dlon
        j0 = math.floor(rj)
        margin = min(margin, rj - j0, 1 - (rj - j0))
        for col in (j0 - 1, j0, j0 + 1):
            # the cell's own offset in time, then its current generation's hash
            a0 = ((row * P.CB_HASH[0]) + (col * P.CB_HASH[1])) & M32
            offset = (mix32(a0 ^ P.CB_SALT_TIME) & 0xFFFF) / 65536
            tc = now + offset * P.CB_PERIOD_S
            gen = math.floor(tc / P.CB_PERIOD_S)
            phase = tc - gen * P.CB_PERIOD_S
            margin = min(margin, abs(tc / P.CB_PERIOD_S - round(tc / P.CB_PERIOD_S)) * 1e3)  # (a generation's edge)
            a = (a0 + gen * P.CB_HASH_GEN) & M32
            h = []
            for salt in P.CB_SALTS:
                b = mix32(a ^ (salt & M32))
                h += [(b & 0xFFFF) / 65536, (b >> 16) / 65536]
            clat = math.radians((row + P.CB_JITTER[0] + P.CB_JITTER[1] * h[0]) * dlat)
            clon = math.radians((col + P.CB_JITTER[0] + P.CB_JITTER[1] * h[1]) * dlon)
            c = (math.cos(clat) * math.cos(clon), math.cos(clat) * math.sin(clon), math.sin(clat))
            d = [c[k] - u[k] for k in range(3)]
            R2 = P.EARTH_RADIUS_M ** 2
            wx = R2 * sum(rows[k][0] * d[k] for k in range(3))
            wz = R2 * sum(rows[k][1] * d[k] for k in range(3))
            k2, k3 = rain(uv[0] + du[0] * wx + du[1] * wz, uv[1] + dv[0] * wx + dv[1] * wz)
            rate = k2 + lerp * (k3 - k2)
            # the evidence: the stations' reports near its centre, else very heavy rain
            est = max((se * (1 - smoothstep(math.dist(c, sv) * P.EARTH_RADIUS_M / 1000.0, *P.CB_EVIDENCE_KM))
                       for sv, se in stations), default=0.0)
            evidence = max(est, smoothstep(rate, *P.CB_RAIN_EVIDENCE) * P.CB_RAIN_EVIDENCE_MAX)
            exist = min(max((evidence * P.CB_CHANCE - h[2]) / P.CB_EXIST_SOFT, 0.0), 1.0)
            stage = phase / (P.CB_LIFE_S[0] + (P.CB_LIFE_S[1] - P.CB_LIFE_S[0]) * h[21])
            margin = min(margin, abs(evidence * P.CB_CHANCE - h[2]) * 1e3, abs(stage - 1.0) * 1e3,
                         abs(stage - P.CB_ANVIL_STAGE) * 1e3)
            if not (exist > 0 and stage < 1.0):
                continue
            w = max(smoothstep(rate, *P.CB_RAIN), est * P.CB_EVIDENCE_W)
            size = h[3] ** P.CB_SIZE_POW
            radius = (P.CB_RADIUS_M[0] + size * (P.CB_RADIUS_M[1] - P.CB_RADIUS_M[0])) * (0.7 + 0.3 * w)
            reported = P.CB_VIGOUR_RAIN + (1 - P.CB_VIGOUR_RAIN) * min(max(est * 1.25, 0.0), 1.0)
            vigour = w * (P.CB_VIGOUR[0] + P.CB_VIGOUR[1] * size) * reported
            thunder = smoothstep(est, *P.CB_THUNDER)
            vigour = max(vigour, P.CB_THUNDER_VIGOUR * thunder)
            # its type: the weights' running sums are the pick's soft edges
            tw = P.CB_TYPE_W
            weights = [tw["single"][0] + tw["single"][1] * (1 - s), tw["sheared"][0] + tw["sheared"][1] * s,
                       tw["multicell"][0] + tw["multicell"][1] * smoothstep(evidence, *P.CB_TYPE_HEAVY),
                       tw["supercell"] * thunder * smoothstep(s, *P.CB_TYPE_SHEAR) * smoothstep(size, *P.CB_TYPE_BIG)]
            edges = [sum(weights[:i + 1]) for i in range(len(weights))]
            pick = h[12] * (edges[-1] + tw["embedded"])
            steps = [min(max((pick - c) / P.CB_TYPE_SOFT + 0.5, 0.0), 1.0) for c in edges]
            ty = {k: vals[0] + sum(g * (b - a) for g, a, b in zip(steps, vals, vals[1:]))
                  for k, vals in P.CB_TYPE_PARAMS.items()}
            # its lean, and the anvil's and the shelf's largest half-widths within the budget
            lean = P.CB_TILT_M * s * ty["tilt"]
            tilt = (lean * wd[0], lean * wd[1])
            anvil_cap = (P.CB_BUDGET_M - lean) / ((1 + P.CB_ANVIL_DOWN * ty["down"] * s) * P.CB_LOBE_MAX * P.CB_FRINGE)
            shelf_cap = (P.CB_BUDGET_M - P.CB_SHELF_SHIFT * lean) / ((1 + P.CB_SHELF_DOWN * s)
                                                                    * (P.CB_LOBE_MAX + P.CB_SHELF_LOBE1))
            share = (P.CB_TOP_SHARE[0] + vigour * (P.CB_TOP_SHARE[1] - P.CB_TOP_SHARE[0])
                     + P.CB_TOP_JITTER * (2 * h[20] - 1))
            level = min(trop * share, P.CB_TOP_MAX_M)
            span = max(level - base, 1.0)
            # the life: birth, growth, decay, death (weaker evidence: younger and older at once)
            birth = smoothstep(stage, 0.0, P.CB_BIRTH) * min(max(exist * 3, 0.0), 1.0)
            grow = smoothstep(stage, 0.0, P.CB_STAGE[0]) * exist
            decay = max(smoothstep(stage, P.CB_STAGE[1], 1.0), 1 - exist)
            death = 1 - smoothstep(stage, P.CB_DEATH, 1.0)
            full = min(trop * share + vigour * grow * (1 - decay) * P.CB_OVERSHOOT_M * ty["overshoot"],
                       P.CB_TOP_MAX_M)
            height = (P.CB_YOUNG_TOP * birth + (1 - P.CB_YOUNG_TOP) * grow) * (1 - P.CB_OLD_SINK * decay) * death
            top = base + (full - base) * height
            dome = min(0.3 * (top - base), P.CB_DOME_M)
            rt0 = max(radius * ty["radius"] * (1 - P.CB_OLD_THIN * decay) * death, 50.0)
            q = (-wx, -wz)  # the column from the centre
            # the updraft tower: leaning downwind and widening up to the anvil level, with bulges;
            # wall cloud
            wall = P.CB_WALL_M * ty["wall"] * min(max(1 - (math.hypot(*q) / (0.5 * rt0)) ** 2, 0.0), 1.0)
            bulges = [(n_ang, lam, amp * ty["bulge"], 2 * math.pi * h[13 + j], 2 * math.pi * h[18 + j] if n_ang else 0.0)
                      for j, (n_ang, lam, amp) in enumerate(P.CB_BULGES)]
            # the mark for the renderer's sky light: 1 near the axis at half the height, fading out
            mk = math.hypot(q[0] - 0.5 * tilt[0], q[1] - 0.5 * tilt[1])
            marks.append(min(max((P.CB_SIDE_REACH * rt0 - mk) / P.CB_SIDE_FADE_M + 1, 0.0), 1.0) * birth)

            def tower(z, q=q, rt0=rt0, span=span, top=top, dome=dome, wall=wall, bulges=bulges, tilt=tilt,
                      band=4 * P.CB_BAND[0] * ty["bands"]):
                f = min(max((z - base) / span, 0.0), 1.0)
                px, pz = q[0] - f * tilt[0], q[1] - f * tilt[1]
                rk = math.hypot(px, pz)
                theta = math.atan2(pz, px)
                bulge = sum(amp * math.sin(2 * math.pi * z / lam + ph)
                            * (math.cos(n_ang * theta - ps) if n_ang else 1.0)
                            for n_ang, lam, amp, ph, ps in bulges)
                bulge += band * f * (1 - f) * math.cos(2 * math.pi * z / P.CB_BAND[1])
                return min(rt0 * (1 + P.CB_WIDEN * f) * (1 + bulge) - rk, top - dome * (rk / rt0) ** 2 - z,
                           z - max(base - wall, terrain + P.CB_WALL_MIN_M))
            parts = [tower]
            # the flanking line: from upwind (random as the wind falls calm), turned to a random side
            ang = 2 * math.pi * h[7]
            b = (s * -wd[0] + (1 - s) * math.cos(ang), s * -wd[1] + (1 - s) * math.sin(ang))
            bn = math.hypot(*b)
            b = (b[0] / bn, b[1] / bn)
            turn = math.radians(P.CB_FLANK_TURN[0] + P.CB_FLANK_TURN[1] * ((2 * h[4]) % 1.0))
            turn = turn if h[4] < 0.5 else -turn
            fd = (b[0] * math.cos(turn) - b[1] * math.sin(turn), b[0] * math.sin(turn) + b[1] * math.cos(turn))
            for i in range(P.CB_FLANK_N):
                f = (h[5] + P.CB_GOLDEN * i) % 1.0
                g = (h[6] + P.CB_GOLDEN * i) % 1.0
                for x in (h[5] + P.CB_GOLDEN * i, h[6] + P.CB_GOLDEN * i):
                    margin = min(margin, abs((x + 0.5) % 1.0 - 0.5) * 1e3)  # (fract's wrap)
                jf = 1 + P.CB_FLANK_JITTER * (2 * f - 1)
                jg = 1 + P.CB_FLANK_JITTER * (2 * g - 1)
                rho = radius * ty["flank"] * jg * (P.CB_FLANK_FROM[0] + P.CB_FLANK_FROM[1] * i)
                rt = radius * (0.5 + 0.5 * ty["flank"]) * jf * (P.CB_FLANK_RADIUS[0] + P.CB_FLANK_RADIUS[1] * i)
                th = span * height * ty["flank_top"] * jf * (P.CB_FLANK_TOP[0] + P.CB_FLANK_TOP[1] * i)
                ri = math.hypot(q[0] - fd[0] * rho, q[1] - fd[1] * rho)
                tt = base + th - min(0.3 * th, P.CB_DOME_M) * (ri / rt) ** 2
                parts.append(lambda z, sd=rt - ri, t=tt: min(sd, t - z, z - base))
            # the lobed ellipses (anvil, mid-level shelf): the normalised radius over the outline
            lobe_amp = 0.5 + 0.5 * h[11]

            def lobed_radius(cx, cz, half, down, back, offsets, one=None, q=q, lobe_amp=lobe_amp, h=h):
                a = (q[0] - cx, q[1] - cz)
                along = a[0] * wd[0] + a[1] * wd[1]
                cross = a[1] * wd[0] - a[0] * wd[1]
                ea = along / (half * (1 + (down if along > 0 else back) * s))
                ec = cross / half
                th = math.atan2(ec, ea)
                m = lobe_amp * sum(amp * math.cos(n * th - (2 * math.pi * h[8 + i] + off))
                                   for i, ((n, amp), off) in enumerate(zip(P.CB_LOBES, offsets)))
                if one:
                    m += one[0] * math.cos(th - one[1])
                return math.hypot(ea, ec) / (1 + m)
            # the anvil around the leaning top and its cirrus fringe: none while growing
            if stage >= P.CB_ANVIL_STAGE:
                agrow = (smoothstep(stage, P.CB_ANVIL_STAGE, P.CB_ANVIL_STAGE + P.CB_ANVIL_GROW)
                         * min(max(exist * 2, 0.0), 1.0) * death)
                ac = min(radius * ty["anvil"] * (P.CB_ANVIL_SCALE[0] + P.CB_ANVIL_SCALE[1] * w)
                         * (1 + P.CB_OLD_SPREAD * decay), anvil_cap)
                ac = max(ac * (0.3 + 0.7 * agrow), 500.0)
                en = lobed_radius(tilt[0], tilt[1], ac, P.CB_ANVIL_DOWN * ty["down"], -P.CB_ANVIL_BACK * ty["back"],
                                  (0.0, 0.0, 0.0))
                anvils.append((min(max(ac * (P.CB_FRINGE - en) / P.CB_CIRRUS_EDGE_M, 0.0), 1.0) * agrow, level))
                thick = ((P.CB_ANVIL_H_M[0] + P.CB_ANVIL_H_M[1] * w) * ty["thick"]
                         * (1 + P.CB_ANVIL_THICK_JITTER * (2 * h[17] - 1)) * agrow)
                atop = level - P.CB_ANVIL_DROOP_M * en * en
                abot = level - thick * (1 - P.CB_ANVIL_THIN * en * en)
                strat = [(ac * (1 - en), atop, abot), (ac * (P.CB_FRINGE - en), atop, atop - agrow * P.CB_FRINGE_H_M)]
            else:
                strat = [(-1e9, -1e9, 0.0), (-1e9, -1e9, 0.0)]
                anvils.append((0.0, level))
            # the mid-level shelf, with a one-sided term
            sh = min(radius * (P.CB_SHELF_SCALE[0] + P.CB_SHELF_SCALE[1] * w), shelf_cap)
            en = lobed_radius(P.CB_SHELF_SHIFT * tilt[0], P.CB_SHELF_SHIFT * tilt[1], sh, P.CB_SHELF_DOWN, 0.0,
                              P.CB_SHELF_PHASE, (P.CB_SHELF_LOBE1, 2 * math.pi * h[16]))
            sthick = ((P.CB_SHELF_H_M[0] + P.CB_SHELF_H_M[1] * w) * ty["shelf"] * smoothstep(w, *P.CB_SHELF_W)
                      * grow * (1 + P.CB_OLD_DEBRIS * decay) * death)
            smid = base + ty["shelf_base"] * span + 0.5 * sthick
            shalf = 0.5 * sthick * (1 - P.CB_SHELF_THIN * en * en)
            strat.append((sh * (1 - en), smid + shalf, smid - shalf))
            towers.append((parts, [lambda z, sd=sd, t=t, bo=bo: min(sd, t - z, z - bo) for sd, t, bo in strat]))
    densities = (P.CB_ANVIL_B, P.CB_FRINGE_B, P.CB_SHELF_B)
    norm = lambda d: min(max(d / P.CB_EDGE_M, -1.0), 1.0)  # noqa: E731
    inside = lambda x: min(max(25 * x + 1, 0.0), 1.0)  # noqa: E731
    out, raw, free = [], [], []
    for k in range(P.WEATHER_SLICES):
        hk = 15.625 * (k + 0.5) ** 2
        xb = norm(max((p(hk) for parts, _ in towers for p in parts), default=-1e9)
                  + P.CB_NOISE_M * (noise[k] - 0.5) * min(max((hk - base) / P.CB_NOISE_BASE_M, 0.0), 1.0))
        xs = [norm(max((st[i](hk) for _, st in towers), default=-1e9)) for i in range(len(densities))]
        b = max([inside(xb) * P.CB_DENSITY_B] + [inside(x) * d for x, d in zip(xs, densities)])
        out.append((min(max(0.5 + 0.5 * max([xb] + xs), 0.0), 1.0), min(max(0.5 + 0.5 * math.sqrt(b), 0.0), 1.0)))
        free.append(1 - inside(xb))
        raw.append(inside(max(xs)) * free[-1])
    # spread one slice up and down outside the bodies
    shares = [max(raw[k], max(raw[j] for j in (k - 1, k + 1) if 0 <= j < len(raw)) * free[k])
              for k in range(len(raw))]
    # the anvils for the renderer's cirrus: the most-covering cell's (first of equals) level and cover
    cov, alt = 0.0, None
    for c, lev in anvils:
        if alt is None or c > cov:
            cov, alt = max(cov, c), lev
    return out, margin, len(towers), shares, (alt, cov), (max(marks, default=0.0), base)


def check_towers(stock, patched_p, tex, check):
    """The cumulonimbus towers (patch_dxil.apply_cb_towers) under a convective rain map: each
    slice's stored x and y are max(MSFS's, the tower's) against towers_ref (float64)."""
    ir = P.Ir(stock)
    planet = P.find_planet_direction(ir)
    rain = P.find_rain_lookup(ir, stock)
    _, h_name = P.column_terrain(ir, stock)
    # MSFS's shape per slice (the patched names: the stock s_k is after the specks, %mod.sp{k}.s0 before)
    sites = P.coverage_sites(ir, P.find_weather_values(ir))

    def msfs(env):
        return ([env[sites[k]["shape"]] for k in range(P.WEATHER_SLICES)],
                [env[f"%mod.sp{k}.s0"] + env[sites[k]["L"]] - 0.25 for k in range(P.WEATHER_SLICES)])
    orig = {}
    for line in ir.lines:
        m = re.match(r"\s+call void @dx\.op\.textureStore\.f32\(i32 67, %dx\.types\.Handle \S+, i32 \S+, i32 \S+, "
                     r"i32 (\d+), float ([^,]+), float ([^,]+),", line)
        if m:
            orig[int(m[1])] = (m[2], m[3])
    tex_r = {**tex, 2: RainTex(2, tex[2].dims), 3: RainTex(3, tex[3].dims)}
    on = [0.0] * (P.CLOUD_PARAMS_SIZE // 4)
    on[0], on[16], on[17] = 1.0, 2.0, 0.33
    cb = constants(0, cloud=on)
    G = cb[(0, 1)]
    rows = [(G[110 + k][0], G[110 + k][2]) for k in range(3)]
    Pc = cb[(0, 2)]
    Pc[5][0], Pc[5][1] = 7.0, -4.0  # a wind (world X/Z, m/s): the cells lean and stream
    G[0][1] = NOW  # (Global[0].y: s since the sim started, for the cells' life cycles)
    cb_off = constants(P.DEBUG_STORMS_OFF, cloud=on)  # (the add-on's thunderstorms off: C32)
    cb_off[(0, 2)][5][0], cb_off[(0, 2)][5][1] = 7.0, -4.0
    cb_off[(0, 1)][0][1] = NOW
    gated = 0
    du, dv, lerp = (Pc[0][0], Pc[0][2]), (Pc[2][0], Pc[2][2]), Pc[6][1]
    worst, columns, slices, skipped, n_towers, worst_share, flat = 0.0, 0, 0, 0, 0, 0.0, 0
    worst_cov, covered, stored = 0.0, 0, 0
    worst_mark, marked = 0.0, 0
    seen = []
    for tx in range(16, 500, 23):
        for ty in range(20, 500, 37):
            m = Machine(patched_p, cb, tex_r, bytearray(16 << 20))
            out = m.run((tx, ty))
            if len(out) != 32:
                continue  # outside the disc
            p = [m.env[c] for c in planet]
            uv = (m.env[rain["u_name"]], m.env[rain["v_name"]])
            seen.append((tx, ty, p))
            want, margin, nt, shares, anvil, mark = towers_ref(p, uv, rows, du, dv, lerp, m.env[h_name],
                                                  lambda u, v: (tex_r[2].sample(u, v)[0], tex_r[3].sample(u, v)[0]),
                                                  (Pc[5][0], Pc[5][1]), msfs(m.env), NOW)
            if margin < 2e-4:
                skipped += 1  # (a cell edge or a chance threshold: float32 and float64 may differ)
                continue
            columns += 1
            n_towers += nt
            if gated < 6 and any(want[k][0] > 0.5 for k in range(P.WEATHER_SLICES)):
                # with bit 8 the column is MSFS's own, and no stratiform shares
                gated += 1
                m_off = Machine(patched_p, cb_off, tex_r, bytearray(16 << 20))
                out_off = m_off.run((tx, ty))
                for k in range(P.WEATHER_SLICES):
                    pre = (m_off.env[orig[k][0]], m_off.env[orig[k][1]])
                    check(out_off[k][:2] == pre and m_off.env[f"%mod.cb.ty{k}"] == 0.0,
                          f"({tx}, {ty}) slice {k}: thunderstorms off (bit 8) but {out_off[k][:2]} != {pre}")
            for k in range(P.WEATHER_SLICES):
                pre = (m.env[orig[k][0]], m.env[orig[k][1]])
                exp = (max(pre[0], want[k][0]), max(pre[1], want[k][1]))
                err = max(abs(out[k][0] - exp[0]), abs(out[k][1] - exp[1]))
                worst = max(worst, err)
                slices += want[k][0] > pre[0] + 0.01
                check(err < 5e-4, f"({tx}, {ty}) slice {k}: towers {out[k][:2]} != {exp} (pre {pre})")
                got = m.env[f"%mod.cb.ty{k}"]
                worst_share = max(worst_share, abs(got - shares[k]))
                flat += shares[k] > 0.5
                # (the share ramps over 320 m of distance; the cell centres' float32 planet-vector
                # differences leave ~1 m: 5e-3 is ~1.6 m)
                check(abs(got - shares[k]) < 5e-3, f"({tx}, {ty}) slice {k}: stratiform share {got} != {shares[k]}")
            # the anvils for the renderer's cirrus: cover and level, and (first thread of a cell, bit 7)
            # where the type writer stores them
            got_alt, got_cov = m.env["%mod.cb.calt"], m.env["%mod.cb.ccov"]
            worst_cov = max(worst_cov, abs(got_cov - anvil[1]))
            check(abs(got_cov - anvil[1]) < 5e-3, f"({tx}, {ty}): anvil cover {got_cov} != {anvil[1]}")
            if anvil[1] > 0.01:
                check(abs(got_alt - anvil[0]) < 1.0, f"({tx}, {ty}): anvil level {got_alt} != {anvil[0]}")
            covered += anvil[1] > 0.5
            # the towers' mark and the base, for the renderer's sky light
            got_mark = m.env["%mod.cb.mark"]
            worst_mark = max(worst_mark, abs(got_mark - mark[0]))
            check(abs(got_mark - mark[0]) < 5e-3, f"({tx}, {ty}): tower mark {got_mark} != {mark[0]}")
            check(abs(m.env["%mod.cb.mbase"] - mark[1]) < 1.0, f"({tx}, {ty}): base {m.env['%mod.cb.mbase']} != {mark[1]}")
            marked += mark[0] > 0.5
            if stored < 3 and tx % 4 == 0 and ty % 4 == 0 and anvil[1] > 0.05:
                stored += 1
                cb_ty = constants(P.DEBUG_BUFFER_TYPES, cloud=on)
                cb_ty[(0, 2)][5][0], cb_ty[(0, 2)][5][1] = 7.0, -4.0
                cb_ty[(0, 1)][0][1] = NOW
                m_ty = Machine(patched_p, cb_ty, tex_r, bytearray(20 << 20))
                m_ty.run((tx, ty))
                a = P.ANVIL_REGION + 8 * ((ty // 4) * P.TYPES_N + tx // 4)
                check(struct.unpack_from("<2f", m_ty.buf, a) == (m_ty.env["%mod.cb.calt"], m_ty.env["%mod.cb.ccov"]),
                      f"({tx}, {ty}): the anvil grid holds {struct.unpack_from('<2f', m_ty.buf, a)}")
                a = P.TOWER_REGION + 8 * ((ty // 4) * P.TYPES_N + tx // 4)
                check(struct.unpack_from("<2f", m_ty.buf, a) == (m_ty.env["%mod.cb.mark"], m_ty.env["%mod.cb.mbase"]),
                      f"({tx}, {ty}): the tower grid holds {struct.unpack_from('<2f', m_ty.buf, a)}")
    check(slices > 0, "towers: no slice of the mock weather got a tower")
    check(covered > 0 and stored > 0, f"towers: anvil cover in {covered} columns, {stored} stored in the grid")
    check(marked > 0, "towers: no column marked for the sky light")
    # with the stations' storm evidence (bit 6 and the table): thunder, CB and TCU reports at three
    # of the columns above
    picks = [seen[len(seen) // 4][2], seen[len(seen) // 2][2], seen[3 * len(seen) // 4][2]]
    stations = [(tuple(np.array(v) / np.linalg.norm(v)), wt * ev)
                for v, wt, ev in zip(picks, (1.0, 1.0, 0.6), (1.0, 0.7, 0.35))]
    table = bytearray(16 << 20)
    o = P.ANCHOR_TABLE
    struct.pack_into("<4I", table, o, P.ANCHOR_MAGIC, 3, P.ANCHOR_STATIONS, 0)
    for i, (v, wt, ev) in enumerate(zip(picks, (1.0, 1.0, 0.6), (1.0, 0.7, 0.35))):
        struct.pack_into("<4f", table, o + 16 + 16 * i, *(np.array(v) / np.linalg.norm(v)), wt)
        row = o + 16 + 16 * P.ANCHOR_STATIONS + 16 * (P.ANCHOR_ROWS * i + P.ANCHOR_ROWS - 1)
        struct.pack_into("<4f", table, row, ev, 1.0, 0.0, 0.0)
    cb_st = constants(P.DEBUG_BUFFER_16MB, cloud=on)
    cb_st[(0, 2)][5][0], cb_st[(0, 2)][5][1] = 7.0, -4.0
    cb_st[(0, 1)][0][1] = NOW
    reported, worst_st = 0, 0.0
    for tx, ty, _ in seen[::2]:
        m = Machine(patched_p, cb_st, tex_r, bytearray(table))
        out = m.run((tx, ty))
        p = [m.env[c] for c in planet]
        uv = (m.env[rain["u_name"]], m.env[rain["v_name"]])
        st = [(sv, float(np.float32(se))) for sv, se in stations]
        want, margin, nt, _, _, _ = towers_ref(p, uv, rows, du, dv, lerp, m.env[h_name],
                                            lambda u, v: (tex_r[2].sample(u, v)[0], tex_r[3].sample(u, v)[0]),
                                            (Pc[5][0], Pc[5][1]), msfs(m.env), NOW, st)
        if margin < 2e-4:
            continue
        _, _, nt0, _, _, _ = towers_ref(p, uv, rows, du, dv, lerp, m.env[h_name],
                                     lambda u, v: (tex_r[2].sample(u, v)[0], tex_r[3].sample(u, v)[0]),
                                     (Pc[5][0], Pc[5][1]), msfs(m.env), NOW)
        reported += nt > nt0
        for k in range(P.WEATHER_SLICES):
            pre = (m.env[orig[k][0]], m.env[orig[k][1]])
            exp = (max(pre[0], want[k][0]), max(pre[1], want[k][1]))
            err = max(abs(out[k][0] - exp[0]), abs(out[k][1] - exp[1]))
            worst_st = max(worst_st, err)
            check(err < 5e-4, f"stations: ({tx}, {ty}) slice {k}: towers {out[k][:2]} != {exp} (pre {pre})")
    check(reported > 0, "towers: the stations' reports added no cell")
    print(f"towers with stations' reports: {len(seen[::2])} columns, {reported} with more cells than from the "
          f"rain alone; max error {worst_st:.1e}")
    check(flat > 0, "towers: no stratiform (anvil, fringe, shelf) slice in the mock weather")
    check(gated > 0, "towers: the thunderstorms-off flag (bit 8) wasn't checked")
    print(f"towers: {columns} columns ({skipped} skipped at thresholds), {n_towers} tower candidates present, "
          f"{slices} slices raised, {flat} stratiform, anvil cover in {covered} columns ({stored} stored), "
          f"{marked} marked; max error {worst:.1e}, shares {worst_share:.1e}, anvil cover {worst_cov:.1e}, "
          f"marks {worst_mark:.1e}")


def check_layers(dxc, index, check):
    base = P.CLOUD_GEN_REGION
    lo = N // 2 - P.CG_VOL_N // 2
    for crc, map2d in LAYER_CRCS.items():
        row = next(r for r in index if r["entry"] == "ch_cloudDensity" and r["crc32"] == crc)
        stock = P.disassemble(ROOT / "extracted" / row["path"], dxc)
        patched, summary = P.patch_cloud_gen_capture(stock)
        print(f"{crc} patched:", summary)
        stock_p, patched_p = Prog(stock), Prog(patched)
        flags = 2 | (P.CG_MAP2D_FLAG if map2d else 0)
        for tid in [(0, 0), (7, 0), (40, 90), (127, 127), (lo + 5, lo + 9), (16, 20), (300, 300)]:
            ref = Machine(stock_p, constants(0), TEX_LAYERS, bytearray(16 << 20)).run(tid)
            off = Machine(patched_p, constants(0), TEX_LAYERS, bytearray(16 << 20))
            check(off.run(tid) == ref, f"{crc} {tid}: volume differs from stock (capture off)")
            check(not any(off.buf), f"{crc} {tid}: wrote with the capture off")
            on = Machine(patched_p, constants(1 | P.DEBUG_BUFFER_16MB), TEX_LAYERS, bytearray(16 << 20))
            check(on.run(tid) == ref, f"{crc} {tid}: volume differs from stock (capture on)")
            buf, (x, y) = on.buf, tid
            if tid == (0, 0):
                check(struct.unpack_from("<4I", buf, base) == (STAMP, P.CG_VERSION, flags, N), f"{crc} header")
                slots = {0: 2, 3: 3, 4: 4 if map2d else None}
                for j in range(len(P.CG_DIM_TEXTURES)):
                    reg = slots.get(j)
                    want = list(TEX_LAYERS[reg].dims) if reg is not None else [0, 0, 0, 0]
                    check(list(struct.unpack_from("<4I", buf, base + P.CG_DIMS + 16 * j)) == want,
                          f"{crc} dims slot {j}")
            if y == 0 and x < P.CG_LUT_N:
                check(struct.unpack_from("<f", buf, base + P.CG_LUT + 4 * x)[0] == TEX_LAYERS[3].load(x, 0, 0)[0],
                      f"{crc} LUT[{x}]")
            if x < P.CG_NOISE_N and y < P.CG_NOISE_N:
                o = base + P.CG_NOISE + (y * P.CG_NOISE_N + x) * 128
                check(list(buf[o:o + 128]) == [u8(c) for k in range(32) for c in TEX_LAYERS[2].load(x, y, k)],
                      f"{crc} {tid}: noise")
            if x < P.CG_MAP_N and y < P.CG_MAP_N:
                o = base + P.CG_MAP[0] + 16 * (y * P.CG_MAP_N + x)
                got = struct.unpack_from("<4f", buf, o)
                want = tuple(TEX_LAYERS[4].load(x, y, 0)) if map2d else (0.0, 0.0, 0.0, 0.0)
                check(got == want, f"{crc} {tid}: 2D map t4")
            expect = [u8(c) for k in range(32) for c in ref[k]] if len(ref) == 32 else None
            cx, cy = x - lo, y - lo
            if 0 <= cx < P.CG_VOL_N and 0 <= cy < P.CG_VOL_N:
                o = base + P.CG_VOL + (cy * P.CG_VOL_N + cx) * 128
                check(expect is not None and list(buf[o:o + 128]) == expect, f"{crc} {tid}: centre volume copy")
            if x % 4 == 0 and y % 4 == 0 and x // 4 < P.CG_VOL_N and y // 4 < P.CG_VOL_N and expect is not None:
                o = base + P.CG_VOLSUB + ((y // 4) * P.CG_VOL_N + x // 4) * 128
                check(list(buf[o:o + 128]) == expect, f"{crc} {tid}: subsampled volume copy")
            print(f"{crc} thread {tid}: {len(ref)} slices stored; {sum(1 for b in buf if b)} bytes dumped")
        # the cloud types: the centre thread writes 0 over the live family's magic (with bit 7 only)
        for f, cleared in ((P.DEBUG_BUFFER_TYPES, True), (0, False)):
            buf = bytearray(20 << 20)
            struct.pack_into("<I", buf, P.TYPES_REGION, P.TYPES_MAGIC)
            m = Machine(patched_p, constants(f), TEX_LAYERS, buf)
            m.run((N // 2, N // 2))
            magic = struct.unpack_from("<I", m.buf, P.TYPES_REGION)[0]
            check(magic == (0 if cleared else P.TYPES_MAGIC), f"{crc} flags {f:#x}: cloud types magic {magic:#x}")


def main():
    dxc = P.find_dxc()
    index = list(csv.DictReader(open(ROOT / "extracted" / "index.csv")))
    row = next(r for r in index if r["entry"] == "ch_cloudDensity" and r["crc32"] == CRC)
    stock = P.disassemble(ROOT / "extracted" / row["path"], dxc)
    patched, summary = P.patch_cloud_gen_capture(stock)
    print("patched:", summary)
    stock_p, patched_p = Prog(stock), Prog(patched)
    base = P.CLOUD_GEN_REGION
    bad = 0

    def check(cond, what):
        nonlocal bad
        if not cond:
            bad += 1
            print("FAIL:", what)

    lo = N // 2 - P.CG_VOL_N // 2
    threads = [(0, 0), (5, 0), (100, 0), (10, 20), (255, 255), (127, 3), (lo + 3, lo + 7), (lo + 127, lo),
               (8, 12), (256, 256), (300, 40), (lo + 4, lo + 8), (511, 511)]
    for tid in threads:
        ref = Machine(stock_p, constants(0), TEX, bytearray(16 << 20)).run(tid)
        off = Machine(patched_p, constants(0), TEX, bytearray(16 << 20))
        out_off = off.run(tid)
        check(out_off == ref, f"{tid}: volume differs from stock (capture off)")
        check(not any(off.buf), f"{tid}: wrote to the debug buffer with the capture off")
        partial = Machine(patched_p, constants(1 | 16), TEX, bytearray(16 << 20))
        partial.run(tid)
        check(not any(partial.buf), f"{tid}: wrote without bit 6 (a smaller debug buffer)")
        on = Machine(patched_p, constants(1 | P.DEBUG_BUFFER_16MB), TEX, bytearray(16 << 20))
        out_on = on.run(tid)
        check(out_on == ref, f"{tid}: volume differs from stock (capture on)")
        buf = on.buf
        x, y = tid
        word = lambda o: struct.unpack_from("<I", buf, o)[0]  # noqa: E731
        flt = lambda o: struct.unpack_from("<f", buf, o)[0]  # noqa: E731
        if tid == (0, 0):
            check([word(base + 4 * k) for k in range(4)] == [STAMP, P.CG_VERSION, 1, N], "header")
            for j, reg in enumerate(P.CG_DIM_TEXTURES):
                d = TEX[reg].dims
                check([word(base + P.CG_DIMS + 16 * j + 4 * k) for k in range(4)] == [d[0], d[1], d[2], d[3]],
                      f"dims of t{reg}")
            cb = constants(1 | P.DEBUG_BUFFER_16MB)
            for r in range(P.CG_USER_REGS):
                check(all(flt(base + P.CG_USER + 16 * r + 4 * k) == f32(cb[(0, 0)][r][k]) for k in range(4)),
                      f"User[{r}]")
            for r in range(P.CG_POST_REGS):
                check(all(flt(base + P.CG_POST + 16 * r + 4 * k) == f32(cb[(0, 2)][r][k]) for k in range(4)),
                      f"PostProcess[{r}]")
        if y == 0 and x < P.CG_LUT_N:
            check(flt(base + P.CG_LUT + 4 * x) == TEX[7].load(x, 0, 0)[0], f"LUT[{x}]")
        if x < P.CG_MAP_N and y < P.CG_MAP_N:
            for j, reg in enumerate((5, 6)):
                o = base + P.CG_MAP[j] + (y * P.CG_MAP_N + x) * 32
                check(list(buf[o:o + 32]) == [u8(TEX[reg].load(x, y, k)[0]) for k in range(32)],
                      f"{tid}: weather map t{reg}")
            for j, reg in enumerate((2, 3)):
                o = base + P.CG_PRECIP + 4 * P.CG_MAP_N * P.CG_MAP_N * j + 4 * (y * P.CG_MAP_N + x)
                check(flt(o) == TEX[reg].load(x, y, 0)[0], f"{tid}: precipitation t{reg}")
        if x < P.CG_NOISE_N and y < P.CG_NOISE_N:
            o = base + P.CG_NOISE + (y * P.CG_NOISE_N + x) * 128
            check(list(buf[o:o + 128]) == [u8(c) for k in range(32) for c in TEX[4].load(x, y, k)],
                  f"{tid}: noise")
        expect = [u8(c) for k in range(32) for c in ref[k]] if len(ref) == 32 else None
        cx, cy = x - lo, y - lo
        if 0 <= cx < P.CG_VOL_N and 0 <= cy < P.CG_VOL_N:
            o = base + P.CG_VOL + (cy * P.CG_VOL_N + cx) * 128
            check(expect is not None and list(buf[o:o + 128]) == expect, f"{tid}: centre volume copy")
        if x % 4 == 0 and y % 4 == 0 and x // 4 < P.CG_VOL_N and y // 4 < P.CG_VOL_N and expect is not None:
            o = base + P.CG_VOLSUB + ((y // 4) * P.CG_VOL_N + x // 4) * 128
            check(list(buf[o:o + 128]) == expect, f"{tid}: subsampled volume copy")
        print(f"thread {tid}: {len(ref)} slices stored; {sum(1 for b in buf if b)} bytes dumped")
    # a thread outside the disc returns early: no volume, but the header and textures still
    corner = Machine(patched_p, constants(1 | P.DEBUG_BUFFER_16MB, disc=0.9), TEX, bytearray(16 << 20))
    check(corner.run((0, 0)) == {}, "(0, 0) outside the disc stored slices")
    check(struct.unpack_from("<4I", corner.buf, base) == (STAMP, P.CG_VERSION, 1, N), "header from an early return")
    o = base + P.CG_MAP[0]
    check(list(corner.buf[o:o + 32]) == [u8(TEX[5].load(0, 0, k)[0]) for k in range(32)], "map from an early return")
    check_layers(dxc, index, check)

    # METAR anchoring: the weather values blended toward the station's layers
    ir = P.Ir(stock)
    values = P.find_weather_values(ir)
    direction = P.find_planet_direction(ir)
    t1_name, h_name = P.column_terrain(ir, stock)
    # the shape noise per slice: the first t4 lookup in each slice
    t4 = P.binding_handle(stock, "t", 4)
    t4_ann = {m[1] for m in re.finditer(rf"^\s+({V}) = call %dx\.types\.Handle @dx\.op\.annotateHandle\(i32 216, "
                                         rf"%dx\.types\.Handle {re.escape(t4)},", stock, re.M)}
    noise = {}
    for line in stock.splitlines():
        m = re.match(rf"\s+({V}) = call %dx\.types\.ResRet\.f32 @dx\.op\.sampleLevel\.f32\(i32 62, %dx\.types\.Handle "
                     rf"({V}), %dx\.types\.Handle \S+, float \S+, float \S+, float ([0-9.e+-]+),", line)
        if m and m[2] in t4_ann:
            k = round(float(m[3]) * 32 - 0.5)
            noise.setdefault(k, m[1])

    TEX_ANCHOR = {**TEX, 7: LutTex(7, (101, 1, 0, 1))}

    def lut(c):  # t7 sampled as the shader does
        return TEX_ANCHOR[7].sample(f32(f32(c * 0.990099) + 0.004950495), 0.0, lod=0.0)[0]
    on_flags = P.DEBUG_BUFFER_16MB  # bit 6 without bit 0: the table is read, nothing captured
    # place stations 1 and 2 on their columns
    for i, tid in STATION_TIDS.items():
        m = Machine(stock_p, constants(0, cloud=anchor_cloud()), TEX_ANCHOR, bytearray(16 << 20))
        m.run(tid)
        u = np.array([m.env[v] for v in direction], dtype=np.float64)
        u /= np.linalg.norm(u)
        STATIONS[i][0] = (math.asin(u[2]), math.atan2(u[1], u[0]))
    table = with_table(STATIONS)
    for tid in [(256, 256), (262, 250), (230, 270), (300, 220), (290, 228), (200, 330), (128, 400), (40, 40)]:
        ref_m = Machine(stock_p, constants(0, cloud=anchor_cloud()), TEX_ANCHOR, bytearray(16 << 20))
        ref = ref_m.run(tid)
        stock_c = [ref_m.env[values[k]] for k in range(P.WEATHER_SLICES)]
        want, dist, w, strat, smooth, dists = anchor_ref(stock_c, [ref_m.env[v] for v in direction], STATIONS)
        on = Machine(patched_p, constants(on_flags, cloud=anchor_cloud()), TEX_ANCHOR, bytearray(table))
        out = on.run(tid)
        got = [on.env[values[k]] for k in range(P.WEATHER_SLICES)]
        err = max(abs(a - b) for a, b in zip(got, want))
        check(err < 1e-5, f"{tid}: anchored weather values differ by {err:.1e} (distance {dist / 1000:.1f} km)")
        # ... and the volume the generator makes of them, against the model
        vol_err = float("nan")
        if generator_model is not None:
            n4 = [tuple(ref_m.env[noise[k]][:4]) for k in range(P.WEATHER_SLICES)]
            res = generator_model.column(want, n4, lut, ref_m.env[t1_name], ref_m.env[h_name], 0.0, strat=strat,
                                         smooth=smooth)
            vol_err = max(max(abs(out[k][0] - res[k][0]), abs(out[k][1] - res[k][1]))
                          for k in range(P.WEATHER_SLICES))
            check(vol_err < 1e-4, f"{tid}: the anchored volume (x, y) differs from the model by {vol_err:.1e}")
        covered = sum(1 for k in range(P.WEATHER_SLICES) if out[k][0] > 0.51)
        # the capture's per-column record: terrain, global terrain, the column's total weight, the nearest station
        cap = Machine(patched_p, constants(1 | P.DEBUG_BUFFER_16MB, cloud=anchor_cloud()), TEX, bytearray(table))
        cap.run(tid)
        n_table = P.CG_END - P.CG_ANCHOR
        check(cap.buf[P.ANCHOR_TABLE:P.ANCHOR_TABLE + n_table] == table[P.ANCHOR_TABLE:P.ANCHOR_TABLE + n_table],
              f"{tid}: the capture overwrote the station table")
        cx, cy = tid[0] - (N // 2 - P.CG_VOL_N // 2), tid[1] - (N // 2 - P.CG_VOL_N // 2)
        if 0 <= cx < P.CG_VOL_N and 0 <= cy < P.CG_VOL_N:
            rec = struct.unpack_from("<4f", cap.buf, P.CLOUD_GEN_REGION + P.CG_COLUMNS + 16 * (cy * P.CG_VOL_N + cx))
            t1, h = P.column_terrain(P.Ir(stock), stock)
            want_rec = (f32(cap.env[t1]), f32(cap.env[h]), f32(cap.env["%mod.ma.w"]), f32(cap.env["%mod.ma.dist"]))
            check(rec == want_rec, f"{tid}: column record {rec} != {want_rec}")
            check(abs(rec[3] - dist) < 2.0 and abs(rec[2] - w) < 1e-4, f"{tid}: recorded distance/weight off")
        moved = sum(1 for a, b in zip(got, stock_c) if abs(a - b) > 1e-6)
        print(f"anchoring at {tid}: stations at " + ", ".join(f"{d / 1000:.1f}" for d in dists)
              + f" km, weight {w:.3f}, {moved} slices moved, max error {err:.1e}; volume vs model {vol_err:.1e}, "
              f"{covered} covered slices")
    # exactly stock without the table, without bit 6, with C20 = 0, with a wrong magic
    for tid in [(256, 256), (300, 220)]:
        ref = Machine(stock_p, constants(0, cloud=anchor_cloud()), TEX, bytearray(16 << 20)).run(tid)
        for what, flags, cloud, buf in (
                ("no table", on_flags, anchor_cloud(), bytearray(16 << 20)),
                ("no bit 6", 16, anchor_cloud(), bytearray(table)),
                ("C20 = 0", on_flags, anchor_cloud(0.0), bytearray(table)),
                ("wrong magic", on_flags, anchor_cloud(), with_table(STATIONS, magic=P.ANCHOR_MAGIC ^ 1))):
            got = Machine(patched_p, constants(flags, cloud=cloud), TEX, buf).run(tid)
            check(got == ref, f"{tid}: anchoring off ({what}) but the volume differs from stock")
    check_types_table(check)
    check_types(stock_p, patched_p, values, h_name, table, TEX_ANCHOR, check)
    check_specks(stock_p, patched_p, values, noise, t1_name, h_name, lut, TEX_ANCHOR, check)
    check_towers(stock, patched_p, TEX_ANCHOR, check)
    # the cloud constants as the shader saw them, in the header
    cap = Machine(patched_p, constants(1 | P.DEBUG_BUFFER_16MB, cloud=anchor_cloud()), TEX, bytearray(table))
    cap.run((0, 0))
    got_c = struct.unpack_from("<32I", cap.buf, P.CLOUD_GEN_REGION + P.CG_CLOUD)
    want_c = tuple(struct.unpack("<I", struct.pack("<f", x))[0] for x in anchor_cloud())
    check(got_c == want_c, "cloud constants in the capture header")
    print("all checks passed" if bad == 0 else f"{bad} check(s) failed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

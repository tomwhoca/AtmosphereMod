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
      27: math.floor, 28: math.ceil, 29: math.trunc}
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
STRENGTH, RADIUS_KM = 0.8, 20.0


class LutTex(Tex):
    """A coverage curve low enough that the anchored layers make cloud from the
    mock noise, so the runs, and with them the dome taper, are exercised."""

    def sample(self, *c, lod=0.0):
        v = f32(0.9 * (1.0 - c[0]))
        return (v, v, v, v)


def anchor_cloud(strength=STRENGTH):
    c = [0.0] * (P.CLOUD_PARAMS_SIZE // 4)
    c[20], c[21] = strength, RADIUS_KM
    return c


def anchor_rows(layers, ceiling):
    """A station's 32 rows (C, a, deck, smooth), as the add-on builds them (cloud_anchor.h rows)."""
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
        rows.append((best, a, st, sm))
    return rows


def unit(lat, lon):
    return np.array([math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)])


def with_table(stations, magic=P.ANCHOR_MAGIC):
    """A debug buffer holding the station table at ANCHOR_TABLE."""
    buf = bytearray(16 << 20)
    o = P.ANCHOR_TABLE
    struct.pack_into("<4I", buf, o, magic, len(stations), P.ANCHOR_STATIONS, 0)
    for i, ((lat, lon), wt, layers, ceiling) in enumerate(stations):
        struct.pack_into("<4f", buf, o + 16 + 16 * i, *unit(lat, lon), wt)
        for k, row in enumerate(anchor_rows(layers, ceiling)):
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

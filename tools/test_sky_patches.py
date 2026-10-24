"""Check the sky-pass patches (patch_dxil.patch_medium with the sky options)
in the DXIL interpreter, on the transmittance LUT and the camera volume:

- off (T0 = 0, C22 = 0, T12 = 0): the output is bit-identical to stock;
- the capture dump (DebugCapture bit 0) doesn't change the output, and lands
  where the add-on reads it: the header, region A (the 13 SkyAtmosphere
  registers) from the transmittance LUT, region B (the camera volume's Global
  and User registers) from the camera volume;
- the daylight white balance (C22 = 1) scales the transmittance by exactly
  SUN_WHITE_BALANCE;
- the far-slice stretch (T12 = 1) changes only the last slice.

The model's numbers themselves are checked on the GPU by atmolab.

    python tools/test_sky_patches.py
"""
import csv
import math
import struct
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import patch_dxil as P  # noqa: E402
from test_cloud_gen_capture import Machine, Prog, Tex, f32  # noqa: E402
from test_medium_patch import ATMO  # noqa: E402

STAMP = 0x2345
# constants from an in-flight capture (FL240): camera Global registers, the
# camera volume's User[1], the light block (sun, moon)
GLOBAL = {
    10: (0.06, 356700.2, 36453.93, 0.0), 51: (-809.3649, 7981.569, -7919.049, 307200.0),
    52: (-13.89603, -309.7211, -17.25389, -15.61003), 56: (0.001953125, 0.001953125, 0.03125, 0.0),
    92: (-225.6554, -1.299219, 6.666667e-06, 0.0), 94: (-811.593, -1.299219, 1.666667e-06, 0.0),
    117: (-2.050772e-06, 7330.939, 5.399343e-07, 0.0), 132: (0.0, 0.0, 0.0, 0.0),
    134: (0.7368613, 0.03505363, 0.6751345, 0.0), 135: (-0.03966696, 0.9991761, -0.008584507, 0.0),
    136: (0.6748792, 0.02045494, -0.7376447, 0.0), 174: (0.7997838, 0.0, 0.0, 2.629284e-05),
    175: (0.0, 0.4298837, 0.0, -0.0002513599), 176: (0.0, 0.0, 0.0, 1.0), 177: (0.0, 0.0, 16.94915, 2.803474e-06),
}
USER1 = (0.03125, 0.03125, 0.015625, 1.0)
LIGHT = {0: (0.7685004, 0.4718744, -0.4321361, 0.004661189), 1: (141102.8, 125271.0, 120217.7, 0.0),
         2: (0.8027644, 0.5958506, 0.0230528, 0.004660385), 3: (0.1211033, 0.1075153, 0.1031784, 0.0)}
# the model's tunables T0..T11 (a hazy day), T12 and T13 from Extra
MODEL = [1.0, 1.5, 0.15, 4.0, 0.6, 2.5, 0.05, 0.1, 1.0, 0.7, 1.3, 1.0]


def bits(u):
    return struct.unpack("<f", struct.pack("<I", u))[0]


def constants(flags=0, t0=0.0, t12=0.0, c22=0.0, layout="transmittance"):
    mod = np.zeros((4, 4))
    mod[0] = (bits(flags), bits(STAMP), t12, 0.0)
    tun = MODEL.copy()
    tun[0] = t0
    mod[1:4] = np.array(tun).reshape(3, 4)
    cloud = np.zeros((8, 4))
    cloud.flat[22] = c22
    atmo = np.array(ATMO, dtype=np.float64)
    if layout == "transmittance":
        return {(0, 0): atmo, (50, 0): mod, (50, 1): cloud}
    glob = np.zeros((300, 4))
    for r, v in GLOBAL.items():
        glob[r] = v
    user = np.zeros((658, 4))
    user[1] = USER1
    light = np.zeros((11, 4))
    for r, v in LIGHT.items():
        light[r] = v
    return {(0, 0): user, (0, 1): glob, (0, 2): atmo, (0, 3): light, (50, 0): mod, (50, 1): cloud}


def same(a, b):
    """Bit-for-bit equal outputs ({slice: (r, g, b, a)}), NaN included."""
    return a.keys() == b.keys() and all(struct.pack("<4f", *a[k]) == struct.pack("<4f", *b[k]) for k in a)


def main():
    dxc = P.find_dxc()
    index = list(csv.DictReader(open(ROOT / "extracted" / "index.csv")))
    bad = 0

    def check(cond, what):
        nonlocal bad
        if not cond:
            bad += 1
            print("FAIL:", what)

    def load(entry):
        rows = sorted({r["path"] for r in index if r["entry"] == entry})
        return [(Path(p).name.split(".")[0], P.disassemble(ROOT / "extracted" / p, dxc)) for p in rows]

    # ---- the transmittance LUT
    for crc, stock in load("ch_transmittance_lut"):
        patched, summary = P.patch_medium(stock, no_phase=True, white_balance=True, dump="sky_atmosphere")
        print(f"ch_transmittance_lut {crc}: {summary}")
        sp, pp = Prog(stock), Prog(patched)
        for tid in [(0, 0), (37, 20), (255, 63), (128, 5)]:
            ref = Machine(sp, constants(), {}, bytearray(1 << 16)).run(tid)
            off = Machine(pp, constants(), {}, bytearray(1 << 16))
            check(same(off.run(tid), ref), f"{crc} {tid}: off differs from stock")
            check(not any(off.buf), f"{crc} {tid}: wrote to the debug buffer without a capture")
            on = Machine(pp, constants(t0=1.0), {}, bytearray(1 << 16)).run(tid)
            check(all(math.isfinite(x) for v in on.values() for x in v), f"{crc} {tid}: model output not finite")
            cap = Machine(pp, constants(flags=1, t0=1.0), {}, bytearray(1 << 16))
            check(same(cap.run(tid), on), f"{crc} {tid}: the capture changed the output")
            buf = cap.buf
            if tid == (0, 0):
                check(struct.unpack_from("<4I", buf, 0) == (P.DEBUG_MAGIC, STAMP, 0, 0), f"{crc}: debug header")
                check(struct.unpack_from("<4I", buf, P.DEBUG_REGION_SKY_ATMOSPHERE) == (STAMP, 13, 0, 0),
                      f"{crc}: region A header")
                got = np.frombuffer(bytes(buf[P.DEBUG_REGION_SKY_ATMOSPHERE + 16:P.DEBUG_REGION_SKY_ATMOSPHERE + 16 + 208]),
                                    np.float32).reshape(13, 4)
                check(np.array_equal(got, np.array(ATMO, np.float32)), f"{crc}: region A registers")
            else:
                check(not any(buf), f"{crc} {tid}: a thread other than 0 dumped")
            wb = Machine(pp, constants(t0=1.0, c22=1.0), {}, bytearray(1 << 16)).run(tid)
            for k in on:
                for c in range(3):
                    want = f32(on[k][c] * f32(P.SUN_WHITE_BALANCE[c]))
                    check(abs(wb[k][c] - want) <= 1e-6 * max(abs(want), 1e-30),
                          f"{crc} {tid}: white balance channel {c}: {wb[k][c]} vs {want}")
        print(f"  off = stock, capture dump and white balance checked at 4 threads")

    # ---- the camera volume
    tex = {0: Tex(0, (512, 512, 0, 1)), 1: Tex(1, (128, 128, 32, 1)), 2: Tex(2, (64, 64, 64, 1)),
           3: Tex(3, (256, 64, 0, 1)), 4: Tex(4, (32, 32, 0, 1))}
    for crc, stock in load("ch_camera_volume"):
        patched, summary = P.patch_medium(stock, far_slice=True, dump="camera")
        print(f"ch_camera_volume {crc}: {summary}")
        sp, pp = Prog(stock), Prog(patched)
        lay = "camera"
        for tid in [(0, 0, 0), (16, 16, 63), (5, 30, 40), (31, 0, 10), (12, 20, 63)]:
            ref = Machine(sp, constants(layout=lay), tex, bytearray(1 << 16)).run(tid)
            off = Machine(pp, constants(layout=lay), tex, bytearray(1 << 16))
            check(same(off.run(tid), ref), f"{crc} {tid}: off differs from stock")
            check(not any(off.buf), f"{crc} {tid}: wrote to the debug buffer without a capture")
            on = Machine(pp, constants(t0=1.0, layout=lay), tex, bytearray(1 << 16)).run(tid)
            check(all(math.isfinite(x) for v in on.values() for x in v), f"{crc} {tid}: model output not finite")
            cap = Machine(pp, constants(flags=1, t0=1.0, layout=lay), tex, bytearray(1 << 16))
            check(same(cap.run(tid), on), f"{crc} {tid}: the capture changed the output")
            if tid == (0, 0, 0):
                buf = cap.buf
                check(struct.unpack_from("<4I", buf, 0) == (P.DEBUG_MAGIC, STAMP, 0, 0), f"{crc}: debug header")
                check(struct.unpack_from("<4I", buf, P.DEBUG_REGION_CAMERA) == (STAMP, 16, 0, 0), f"{crc}: region B header")
                want = [GLOBAL[r] for r in P.CAMERA_DUMP_GLOBAL] + [USER1] + [(0.0,) * 4] * 5
                got = np.frombuffer(bytes(buf[P.DEBUG_REGION_CAMERA + 16:P.DEBUG_REGION_CAMERA + 16 + 16 * 21]),
                                    np.float32).reshape(21, 4)
                check(np.array_equal(got, np.array(want, np.float32)), f"{crc}: region B registers")
            stretched = Machine(pp, constants(t0=1.0, t12=1.0, layout=lay), tex, bytearray(1 << 16)).run(tid)
            if tid[2] == 63:
                check(not same(stretched, on), f"{crc} {tid}: T12 didn't change the last slice")
            else:
                check(same(stretched, on), f"{crc} {tid}: T12 changed slice {tid[2]}")
        print(f"  off = stock, capture dump and far-slice stretch checked at 5 threads")
    print("all checks passed" if bad == 0 else f"{bad} check(s) failed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()

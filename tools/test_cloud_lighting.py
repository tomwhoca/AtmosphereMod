"""Check the cloud lighting patches (C0-C19, C28-C31).

    python tools/test_cloud_lighting.py                 # every ch_volumetric_clouds_render variant
    python tools/test_cloud_lighting.py 0xda73240c ...  # some variants

Evaluates the patched IR (patch_clouds, as built) with the float32
interpreter and compares, for several settings:
  - forward lobe (apply_cloud_phase), every sun and moon phase blend, over
    all scattering angles:
    our lobe HG(C1) takes weight w = C0 C2 (0 to 0.5) from the stock forward lobe;
  - multiple scattering (apply_cloud_ms) and the build-up of bounced light
    (apply_cloud_shape), the sun phase per channel:
    ((P' + rain - P_ms) s^2 (1 - C0 C3) + P_ms) (1 - C0 C6 v exp(-depth / C7))
    + glory_c s^2 (1 - C0 C3), all times the aircraft's shadow factor,
    P_ms = HG(C0 C4), v = ((1 - cos) / 2)^2,
    glory_c = 0.1 C0 C9 (1 + D (1 - e^(-m z^2)) sin(2z + phase)) / (pi z / 2 + e^(-k z)),
    D = exp(-2 (spread z)^2), z = 2 pi r / lambda_c * sqrt(2 (1 + cos)), r = C10, spread = C11;
  - the aircraft's shadow factor (C12-C15) on a grid of view directions
    around the antisolar point, against a numpy model of the silhouette;
  - the soft transmittance at every site, with C5 setting its floor (stock
    when off);
  - every boundary ramp r, sharpened about one half: sat((r - 0.5) (1 + C0 C8) + 0.5), the view
    march's no steeper than max(160, step) / step;
  - the view march's cloud extinction (apply_cloud_optical), ramp * sigma' with
    sigma' = lerp(C16 0.01 (sigma / 0.01)^C17, C19 sigma, sat((h - C18 + 3 km) / 3 km));
  - the surface detail (apply_cloud_detail): every march's 1 km octave
    scaled by a = 1 - C28 (1 - smoothstep(D / C29)), D the height above the
    cloud base; and in the view march a finer octave + C30 a (n3 - 0.75), n3
    at a C31 m period.
Every setting that is off must be bit-identical to the stock IR.
"""

import csv
import math
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import patch_dxil as P  # noqa: E402
from test_medium_patch import Evaluator, F  # noqa: E402

# C0..C15: all off, each part alone, the user's settings plus steps 2-5, clamps, negatives
SETS = [
    (0.0, 1.0, 0.5, 0.4, 0.4, 0.1, 0.5, 150, 1.0, 2.0, 6.0, 0.08, 0.0, 0.0, 0.0, 0.0),
    (1.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (1.0, 0.9, 0.3, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (1.0, 1.0, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (1.0, 0.9, 0.0, 0.4, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (1.0, 0.9, 0.0, 0.0, 0.4, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (1.0, 0.9, 0.0, 0.0, 0.0, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (1.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.5, 150, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (1.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (1.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (1.0, 1.0, 0.5, 0.4, 0.4, 0.06, 0.6, 200, 2.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (0.5, 0.8, 0.2, 0.3, 0.6, 0.05, 0.3, 80, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (1.0, 1.5, 0.8, 2.0, 1.5, 5.0, 3.0, 0.5, 20.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (1.0, -0.2, -1.0, -0.5, -0.5, -1.0, -1.0, -10.0, -1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (1.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    (1.0, 1.0, 0.5, 0.4, 0.4, 0.06, 0.5, 150, 1.0, 2.0, 6.0, 0.08, 0.0, 0.0, 0.0, 0.0),
    (0.5, 0.9, 0.0, 0.3, 0.0, 0.0, 0.0, 0.0, 0.0, 1.5, 15.0, 0.3, 0.0, 0.0, 0.0, 0.0),
    (1.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 3.0, 100.0, 2.0, 0.0, 0.0, 0.0, 0.0),
    (1.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, -1.0, -5.0, -1.0, 0.0, 0.0, 0.0, 0.0),
    (1.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.8, 0.5, 0.2, 35.0),
    (1.0, 1.0, 0.5, 0.4, 0.4, 0.06, 0.5, 150, 1.0, 1.5, 0.0, 0.0, 1.0, -2.0, -0.4, 11.0),
    (1.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.8, 0.5, 0.2, 0.0),
    (0.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.8, 0.5, 0.2, 35.0),
    (1.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 3.0, 7.0, 3.5, 60.0),
    # C16-C19: optical thickness (factor, profile exponent, ice altitude, ice factor)
    (1.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 3.0, 0.33, 0.0, 0.0),
    (1.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 8000.0, 2.5),
    (1.0, 1.0, 0.5, 0.4, 0.4, 0.06, 0.5, 150, 1.0, 1.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 2.0, 0.5, 3000.0, 0.5),
    (0.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 3.0, 0.33, 0.0, 0.0),
    (1.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 50.0, 0.05, -5.0, -1.0),
    (1.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.5, 3.0, 12000.0, 30.0),
    (1.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, -1.0, 0.33, 0.0, 2.0),
    # C28-C31: surface detail (base smoothing, its depth, finer octave, its period); C20-C27 are the anchor's
    (1.0, 0.9) + (0.0,) * 26 + (1.0, 0.0, 0.0, 0.0),
    (1.0, 0.9) + (0.0,) * 26 + (0.5, 500.0, 0.02, 200.0),
    (1.0, 0.9) + (0.0,) * 26 + (0.0, 0.0, 0.03, 0.0),
    (0.0, 0.9) + (0.0,) * 26 + (1.0, 0.0, 0.03, 0.0),
    (1.0, 0.9) + (0.0,) * 26 + (2.0, -5.0, 0.5, 5000.0),
    (0.6, 1.0, 0.5, 0.4, 0.4, 0.06, 0.5, 150, 1.0, 1.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 2.0, 0.5, 0.0, 2.0)
    + (0.0,) * 8 + (1.0, 300.0, 0.02, 350.0),
]


def type_set(c0=1.0, c8=2.0, c16=4.0, c17=0.33, c18=0.0, strength=1.0, thick=(0.6, 0.5, 0.4, 0.3)):
    c = [0.0] * 32
    c[0], c[1], c[8], c[16], c[17], c[18], c[19] = c0, 0.9, c8, c16, c17, c18, 1.5
    c[23], c[24:28] = strength, list(thick)
    return tuple(c)


# C23-C27: cloud types (strength, the thicknesses of Sc, St, Ac, As), with the grid
TYPE_SETS = [type_set(), type_set(strength=0.5), type_set(c8=0.0), type_set(c16=0.0), type_set(c0=0.5),
             type_set(thick=(1.5, -1.0, 0.0, 1.0)), type_set(c17=0.0, c8=8.0), type_set(c18=3000.0, strength=2.0)]
TYPE_OFFSET = 64.0 / 512  # the header's 64 / N


def fine_sampler(u, v, w=None):
    """A smooth mock of the detail noise for the finer octave's lookup."""
    u, v = np.asarray(u, np.float64), np.asarray(v, np.float64)
    w = np.zeros_like(u) if w is None else np.asarray(w, np.float64)
    n = 0.5 + 0.45 * np.sin(7.3 * u + 3.1 * v + 5.7 * w)
    return np.stack([n, np.zeros_like(n), np.zeros_like(n), np.zeros_like(n)], -1).astype(F)
N = 20001


def hg(g, c):
    return (1 - g * g) / (4 * math.pi * (1 + g * g - 2 * g * c) ** 1.5)


def clamp(x, lo, hi):
    return min(max(x, lo), hi)


def compare(got, ref, tol99, tolmax, what, failures):
    err = np.nan_to_num(np.abs(got - ref) / np.maximum(np.abs(ref), 1e-12), nan=np.inf)
    e99, emax = float(np.percentile(err, 99.9)), float(err.max())
    if e99 > tol99 or emax > tolmax:
        failures.append(f"{what}: rel. error p99.9 {e99:.1e}, max {emax:.1e}")
    return e99


def light_reg(ir, v):
    """True if v is a component of Light c0 (the sun direction)."""
    if v in ir.extracts and ir.extracts[v][0] in ir.loads:
        handle, reg = ir.loads[ir.extracts[v][0]]
        return ir.cbs.get(handle, ("", 0))[1] == 176 and reg == 0
    return False


def shadow_off(c):
    return clamp(c[0], 0, 1) * clamp(c[12], 0, 1) <= 0 or c[15] <= 0


def shadow_ref(view, sun, dist, c, s=None):
    """Independent numpy model of the aircraft's shadow factor (C12-C15);
    s: the sample's saturated sun visibility (1 if None)."""
    if shadow_off(c):
        return np.ones(len(dist))
    strength = clamp(c[0], 0, 1) * clamp(c[12], 0, 1)
    span = max(c[15], 0.0)
    up = np.array([0.0, 1.0, 0.0])
    sh = np.array([sun[0], 0.0, sun[2]]) / math.hypot(sun[0], sun[2])
    fwd = sh * math.cos(c[13]) + np.cross(sh, up) * math.sin(c[13])  # clockwise from above
    right = np.cross(fwd, up) * math.cos(c[14]) - up * math.sin(c[14])
    e1 = fwd - fwd.dot(sun) * sun
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(sun, e1)
    cosv = view @ sun
    off = view - cosv[:, None] * sun
    q = dist[:, None] * np.stack([off @ e1, off @ e2], axis=1)
    behind = -dist * cosv
    smear = (P.CLOUD_STEP_MIN_M + P.CLOUD_STEP_PER_M * dist) * np.sqrt(np.maximum(1 - cosv * cosv, 0))
    pw = np.maximum(np.maximum(behind * P.SUN_DIAMETER_TAN, smear), 0.05)
    occ = np.zeros(len(dist))
    for (a0, b0), (a1, b1), r in P.shadow_capsules(span):
        A = np.array([(a0 * fwd + b0 * right) @ e1, (a0 * fwd + b0 * right) @ e2])
        B = np.array([(a1 * fwd + b1 * right) @ e1, (a1 * fwd + b1 * right) @ e2])
        D = B - A
        h = np.clip(((q - A) @ D) / max(D @ D, 1e-6), 0, 1)
        d = np.linalg.norm(q - A - h[:, None] * D, axis=1)
        occ = np.maximum(occ, np.clip((r - d) / pw + 0.5, 0, 1) * np.clip(2.5 * r / pw, 0, 1))
    occ = np.where(behind > 0, occ, 0.0)
    if s is not None:
        occ = occ * np.asarray(s, dtype=np.float64) ** 2
    return 1 - strength * occ


def check_shadow(patched, dir_names, light_names, t_name, s_name, failures):
    """The shadow factor on a grid of view directions within 4 deg of the
    antisolar point, at several distances, against shadow_ref."""
    n = 161
    sun = np.array([math.cos(math.radians(32)) * math.sin(math.radians(-70)), math.sin(math.radians(32)),
                    math.cos(math.radians(32)) * math.cos(math.radians(-70))])
    a1 = np.cross(sun, [0.0, 1.0, 0.0])
    a1 /= np.linalg.norm(a1)
    a2 = np.cross(sun, a1)
    u, v = np.meshgrid(np.linspace(-0.07, 0.07, n), np.linspace(-0.07, 0.07, n))
    view = -sun + u.ravel()[:, None] * a1 + v.ravel()[:, None] * a2
    view /= np.linalg.norm(view, axis=1, keepdims=True)
    worst = 0.0
    for c in [(1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1.0, 0.7, 0.25, 35.0),
              (1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.6, -2.5, -0.5, 11.0)]:
        cloud = np.zeros((4, 4))
        cloud.flat[:16] = c
        for dist in (150.0, 700.0, 2500.0):
            sv = np.linspace(0.3, 1.0, len(view)).astype(F)  # the direct-sunlight weight, and 1
            sv[: len(view) // 2] = 1.0
            seeds = {t_name: np.full(len(view), dist, dtype=F), s_name: sv}
            for k3 in range(3):
                seeds[dir_names[k3]] = view[:, k3].astype(F)
                seeds[light_names[k3]] = np.full(len(view), sun[k3], dtype=F)
            seeds[LV] = np.ones(len(view), F)
            got = Evaluator(patched, {"b1 space50": cloud, "b0 space50": np.zeros((4, 4))}, seeds)
            value = got("%mod.sh.f").astype(np.float64)
            ref = shadow_ref(view, sun, np.full(len(view), dist), c, sv.astype(np.float64))
            err = float(np.nan_to_num(np.abs(value - ref), nan=np.inf).max())
            worst = max(worst, err)
            shaded = float((ref < 0.99).mean())
            if err > 2e-3 or shaded <= 0:
                failures.append(f"shadow C12-C15={c[12:]} at {dist:.0f} m: abs. error max {err:.1e}, "
                                f"{shaded:.1%} of the grid shaded")
    return worst


MAP_FRAME = 0x2A51
LV = "%mod.lv.sat"  # the cloud light volume's sun visibility at the camera (light_volume_at_camera)


def window_buffer(sun, c0, flag_frame, vcam=1.0, header_frame=None):
    """A synthetic live window (P.SIL_REGION) for a sun direction: rows
    world -> level-0 cells around the camera (the window's centre), and a
    pyramid of a cross-shaped silhouette (fuselage and wings, rasterised 4 x 4
    per level-0 cell, box-filtered up, each level's outer ring 0) as the
    debug buffer's float32 words. Also returns the levels and rows."""
    e1 = np.cross(sun, [0.0, 1.0, 0.0])
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(sun, e1)
    n0 = P.SIL_N0
    sub = (np.arange(n0 * 4) + 0.5) / 4 - n0 / 2          # level-0 sub-cell centres, cells from the camera
    mu, mv = np.meshgrid(sub * c0, sub * c0)                # metres in the light plane (x = e1, y = e2)
    fuselage = (np.abs(mv - 3.0) < 2.0) & (mu > -30) & (mu < 4)
    wing = (np.abs(mu + 12) < 2.5) & (np.abs(mv - 3.0) < 16)
    fine = (fuselage | wing).astype(np.float64)
    raw = [fine.reshape(n0, 4, n0, 4).mean(axis=(1, 3))]
    for L in range(1, P.SIL_LEVELS):
        n = n0 >> L
        raw.append(raw[-1].reshape(n, 2, n, 2).mean(axis=(1, 3)))
    out = []
    for lv in raw:
        lv = lv.copy()
        lv[0, :] = lv[-1, :] = lv[:, 0] = lv[:, -1] = 0
        out.append(lv)
    words = np.zeros((P.SIL_REGION + P.SIL_OFFSETS[-1]) // 4 + 16, np.float32)
    base = P.SIL_REGION // 4
    words[base] = np.array([(flag_frame & 0xFFFF) | 0x10000], np.int32).view(np.float32)[0]
    words[base + 1:base + 4] = (c0, 1 / 2429.0, 0.79)
    words[base + 4:base + 8] = (*(e1 / c0), n0 / 2)
    words[base + 8:base + 12] = (*(e2 / c0), n0 / 2)
    hf = flag_frame if header_frame is None else header_frame
    words[base + 20] = vcam                                   # the sunlight at the camera
    words[base + 21] = np.array([(hf & 0xFFFF) | 0x10000], np.int32).view(np.float32)[0]
    for L in range(P.SIL_LEVELS):
        o = (P.SIL_REGION + P.SIL_OFFSETS[L]) // 4
        words[o:o + out[L].size] = out[L].ravel()
    return words, out, e1, e2


def map_ref(view, sun, dist, c, s, levels, e1, e2, c0):
    """numpy model of the window lookup (shadow_map_sample_lines)."""
    strength = clamp(c[0], 0, 1) * clamp(c[12], 0, 1)
    if strength <= 0:
        return np.ones(len(dist))
    n0 = P.SIL_N0
    p = dist[:, None] * view
    x = p @ (e1 / c0) + n0 / 2
    y = p @ (e2 / c0) + n0 / 2
    cosv = view @ sun
    behind = -dist * cosv
    smear = (P.CLOUD_STEP_MIN_M + P.CLOUD_STEP_PER_M * dist) * np.sqrt(np.maximum(1 - cosv * cosv, 0))
    pw = np.maximum(np.maximum(behind * P.SUN_DIAMETER_TAN, smear), 0.05)
    pwc = pw / c0
    lmax = P.SIL_LOOKUP_LEVELS
    lf = np.clip(np.log2(np.maximum(pwc, 1e-3)), 0, lmax)
    l0 = np.floor(lf)
    wl = lf - l0
    l1 = np.minimum(l0 + 1, lmax)

    def bilinear(lv):
        out = np.zeros(len(dist))
        for L in range(lmax + 1):
            sel = lv == L
            if not sel.any():
                continue
            n = n0 >> L
            xl, yl = x[sel] / 2 ** L - 0.5, y[sel] / 2 ** L - 0.5
            fx, fy = np.floor(xl), np.floor(yl)
            ax, ay = xl - fx, yl - fy

            def at(cx, cy):
                return levels[L][np.clip(cy, 0, n - 1).astype(int), np.clip(cx, 0, n - 1).astype(int)]
            top = at(fx, fy) + (at(fx + 1, fy) - at(fx, fy)) * ax
            bottom = at(fx, fy + 1) + (at(fx + 1, fy + 1) - at(fx, fy + 1)) * ax
            out[sel] = top + (bottom - top) * ay
        return out
    c0v, c1v = bilinear(l0), bilinear(l1)
    cov = c0v + (c1v - c0v) * wl
    big = np.maximum(pwc / 2 ** lmax, 1)
    occ = np.where(behind > 0, cov / big ** 2, 0.0) * s ** 2
    return 1 - strength * occ


def check_shadow_map(patched, dir_names, light_names, t_name, s_name, failures):
    """The live window (Raytraced Shadows off): the shadow factor with
    DebugCapture bit 4 and a current flag against map_ref, over a grid around
    the antisolar point, at several distances; with a stale flag the capsules
    (cockpit) or nothing (external view, C15 < 0)."""
    n = 121
    sun = np.array([math.cos(math.radians(32)) * math.sin(math.radians(-70)), math.sin(math.radians(32)),
                    math.cos(math.radians(32)) * math.cos(math.radians(-70))])
    a1 = np.cross(sun, [0.0, 1.0, 0.0])
    a1 /= np.linalg.norm(a1)
    a2 = np.cross(sun, a1)
    worst = 0.0
    c0 = 0.33
    glory_cloud = np.zeros((4, 4))
    glory_cloud.flat[:16] = (1, 0, 0, 0, 0, 0, 0, 0, 0, 1.0, 8.0, 0.2, 0.8, 0.7, 0.25, 35.0)
    gview = -sun + np.linspace(-0.1, 0.1, 64)[:, None] * a1
    gview /= np.linalg.norm(gview, axis=1, keepdims=True)
    glory_seeds = {t_name: np.full(len(gview), 400.0, dtype=F), s_name: np.ones(len(gview), F)}
    for k3 in range(3):
        glory_seeds[dir_names[k3]] = gview[:, k3].astype(F)
        glory_seeds[light_names[k3]] = np.full(len(gview), sun[k3], dtype=F)
    cases = ((MAP_FRAME, True, 1.0, MAP_FRAME, MAP_FRAME), (MAP_FRAME + 1, True, 1.0, MAP_FRAME, MAP_FRAME),
             (MAP_FRAME + 3, False, 1.0, MAP_FRAME, MAP_FRAME),
             # under a cloud: the sunlight at the camera scales the shadow (window or capsules)
             (MAP_FRAME, True, 0.4, MAP_FRAME, MAP_FRAME), (MAP_FRAME, False, 0.25, MAP_FRAME, MAP_FRAME - 9),
             # a stale header: its sunlight is ignored
             (MAP_FRAME, True, 0.4, MAP_FRAME - 9, MAP_FRAME))
    cases += ((MAP_FRAME, True, 1.0, MAP_FRAME, MAP_FRAME, 0.3), (MAP_FRAME, False, 0.5, MAP_FRAME, MAP_FRAME - 9, 0.5))
    for frame, current, vcam, hframe, fframe, *lv in cases:
        lv = lv[0] if lv else 1.0
        words, levels, e1, e2 = window_buffer(sun, c0, fframe, vcam, hframe)
        gate = (vcam if ((frame - hframe) & 0xFFFF) <= P.SIL_FRESH_FRAMES else 1.0) * lv
        mod = np.zeros((4, 4))
        mod[0, 0] = np.array([(frame & 0xFFFF) << 16 | 16 | 12], np.uint32).view(np.float32)[0]
        for c15 in (35.0, -35.0):
            c = (1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.8, 0.7, 0.25, c15)
            cloud = np.zeros((4, 4))
            cloud.flat[:16] = c
            for dist, extent in ((60.0, 0.6), (400.0, 0.12), (2500.0, 0.03)):
                u, v = np.meshgrid(np.linspace(-extent, extent, n), np.linspace(-extent, extent, n))
                view = -sun + u.ravel()[:, None] * a1 + v.ravel()[:, None] * a2
                view /= np.linalg.norm(view, axis=1, keepdims=True)
                sv = np.linspace(0.3, 1.0, len(view)).astype(F)
                sv[: len(view) // 2] = 1.0
                seeds = {t_name: np.full(len(view), dist, dtype=F), s_name: sv}
                for k3 in range(3):
                    seeds[dir_names[k3]] = view[:, k3].astype(F)
                    seeds[light_names[k3]] = np.full(len(view), sun[k3], dtype=F)
                seeds[LV] = np.full(len(view), lv, F)
                got = Evaluator(patched, {"b1 space50": cloud, "b0 space50": mod}, seeds, buffer=words)
                value = got("%mod.sh.f").astype(np.float64)
                d = np.full(len(view), dist)
                s64 = sv.astype(np.float64)
                if current:
                    ref = map_ref(view, sun, d, c, s64, levels, e1, e2, c0)
                    what = "window"
                else:
                    ref = shadow_ref(view, sun, d, c, s64)  # capsules, or 1 with C15 < 0
                    what = "stale window -> capsules" if c15 > 0 else "stale window, external view -> off"
                ref = 1 - (1 - ref) * gate
                what += f" (sunlight at the camera {gate:g})"
                err = np.nan_to_num(np.abs(value - ref), nan=np.inf)
                worst = max(worst, float(np.percentile(err, 99.9)))
                shaded = float((ref < 0.99).mean())
                if np.percentile(err, 99.9) > 2e-3 or err.max() > 0.05 or (current and gate > 0.3 and shaded <= 0.01):
                    failures.append(f"{what}, C15 {c15:+.0f}, frame +{frame - MAP_FRAME}, {dist:.0f} m: abs. error "
                                    f"p99.9 {np.percentile(err, 99.9):.1e}, max {err.max():.1e}; "
                                    f"{shaded:.1%} shaded")
            # the glory scales with the sunlight at the camera too
            glory = sorted(set(re.findall(r"^\s+(%mod\.gl\.p\d) = ", patched, re.M)))
            base_ev = Evaluator(patched, {"b1 space50": glory_cloud, "b0 space50": np.zeros((4, 4))},
                                {**glory_seeds, LV: np.ones(len(gview), F)})
            gated_ev = Evaluator(patched, {"b1 space50": glory_cloud, "b0 space50": mod},
                                 {**glory_seeds, LV: np.full(len(gview), lv, F)}, buffer=words)
            for g in glory:
                b, w = base_ev(g).astype(np.float64), gated_ev(g).astype(np.float64)
                if np.abs(w - b * gate).max() > 1e-6 * max(1.0, np.abs(b).max()):
                    failures.append(f"glory {g}: not scaled by the sunlight at the camera {gate:g}")
    return worst


def check_light_volume(stock, patched, failures):
    """light_volume_at_camera's copy of the cloud light volume lookup against
    the stock chain evaluated with the march at the camera (distance 0,
    position = the march's base), with random constants and a mock 3D map."""
    ir = P.Ir(stock)
    ms = P.find_cloud_ms(ir)
    _, vis_name, subst = P.light_volume_at_camera(ir, ms, ir.defs[ms["cos"]])
    vis = vis_name.replace("%mod.lv.", "%")
    rng = np.random.default_rng(11)
    cbs = {}
    for _, (_, size) in ir.cbs.items():
        cbs[size] = rng.normal(0, 0.3, size=(size // 16, 4))
    for key in ("b0 space50", "b1 space50"):
        cbs[key] = np.zeros((4, 4))

    def sampler(u, v, w=None):
        u, v = np.asarray(u, np.float64), np.asarray(v, np.float64)
        w = np.zeros_like(u) if w is None else np.asarray(w, np.float64)
        x = 0.5 + 0.4 * np.sin(3 * u + 2 * v - w)
        return np.stack(np.broadcast_arrays(x, 1 - x, x * x, 0.5), -1)
    outer = {k: np.full(4, 750.0, F) for k, v in subst.items() if v == k}
    # the pixel (blue-noise jitter of the lookup): thread ids, and a mock noise texture
    for name in re.findall(r"^\s+(%\d+) = call i32 @dx\.op\.threadId\.i32", stock, re.M):
        outer[name] = np.array([0, 5, 17, 300], np.int64)

    def texture(x, y, z):
        x, y, z = np.broadcast_arrays(np.asarray(x), np.asarray(y), np.asarray(z))
        return np.stack(np.broadcast_arrays((x * 7 + y * 3 + z) % 64 / 64.0, 0.25, 0.75, 0.5), -1).astype(F)
    base_ev = Evaluator(stock, cbs, dict(outer), sampler=sampler, texture=texture)
    seeds = dict(outer)
    for phi, value in subst.items():
        if value == phi:
            continue
        seeds[phi] = np.full(4, 0.0, F) if value == "0.000000e+00" else np.asarray(base_ev(value), F) * np.ones(4, F)
    want = Evaluator(stock, cbs, seeds, sampler=sampler, texture=texture)(vis).astype(np.float64)
    got = Evaluator(patched, cbs, dict(outer), sampler=sampler, texture=texture)(vis_name).astype(np.float64)
    err = float(np.abs(got - want).max())
    if not np.isfinite(err) or err > 1e-5:
        failures.append(f"light volume at the camera: {got[0]:.6f} vs MSFS's own {want[0]:.6f}")
    return err


def types_buffer(rng, magic=P.TYPES_MAGIC):
    """The debug buffer (float32 words) with the generator's header and a
    random grid of type weights (sum <= 1; some cells all cumulus), and the
    grid as bytes [slice, y, x, type], the anvils' (level, cover) and the
    towers' (mark, base) per cell."""
    n, k = P.TYPES_N, P.TYPES_SLICES
    raw = rng.dirichlet(np.ones(5), size=(k, n, n))[..., :4]
    raw[rng.uniform(size=(k, n, n)) < 0.2] = 0.0
    grid = np.floor(raw * 255).astype(np.uint8)
    words = np.zeros(P.TOWER_REGION // 4 + 2 * n * n, np.uint32)
    words[P.TYPES_REGION // 4:P.TYPES_REGION // 4 + 4] = (magic, 7, 512, np.float32(TYPE_OFFSET).view(np.uint32))
    words[(P.TYPES_REGION + 16) // 4:P.ANVIL_REGION // 4] = grid.reshape(-1, 4).copy().view(np.uint32).ravel()
    # the storm cells' anvils: (level, cover) per cell, some cells without
    anvil = np.stack([rng.uniform(8000, 13000, (n, n)), rng.uniform(0, 1, (n, n))], -1).astype(np.float32)
    anvil[..., 1][rng.uniform(size=(n, n)) < 0.4] = 0.0
    words[P.ANVIL_REGION // 4:P.TOWER_REGION // 4] = anvil.reshape(-1).view(np.uint32)
    # the storm towers' marks: (mark, base) per cell, most cells without
    tower = np.stack([rng.uniform(0, 1, (n, n)), rng.uniform(300, 2500, (n, n))], -1).astype(np.float32)
    tower[..., 0][rng.uniform(size=(n, n)) < 0.4] = 0.0
    words[P.TOWER_REGION // 4:] = tower.reshape(-1).view(np.uint32)
    return words.view(np.float32), (grid, anvil, tower)


def types_ref(c, grids, u, v, w):
    """Per lane, float64: the thickness factor T, the base mix m, the view
    ramp's sharpening, the puff factor (cloud types), the anvils' cover and
    cover-weighted level, and the towers' mark and mark-weighted base."""
    grid, anvil, tower = grids
    n = P.TYPES_N
    cell = []
    for coord in (u, v):
        f = np.clip(coord.astype(F) * F(n) + F(-TYPE_OFFSET), 0, n - 1).astype(np.float64)
        f0 = np.minimum(np.floor(f), n - 2)
        cell.append((f0.astype(int), f - f0))
    (x0, ax), (y0, ay) = cell
    k = np.minimum(w.astype(F) * F(P.TYPES_SLICES), P.TYPES_SLICES - 1).astype(int)
    g = grid.astype(np.float64) / 255

    def at(dx, dy):
        return g[k, y0 + dy, x0 + dx]
    wts = ((1 - ax) * (1 - ay))[:, None] * at(0, 0) + (ax * (1 - ay))[:, None] * at(1, 0) \
        + ((1 - ax) * ay)[:, None] * at(0, 1) + (ax * ay)[:, None] * at(1, 1)
    c0 = clamp(c[0], 0.0, 1.0)
    s = c0 * clamp(c[23], 0.0, 1.0)
    thick = np.array([clamp(x, 0.0, 1.0) for x in c[24:28]])
    T = 1 - s * (wts * (1 - thick)).sum(1)
    m = s * (wts * np.array(P.TYPES_BASE_MIX)).sum(1)
    cu = np.maximum(1 - wts.sum(1), 0)
    ce = c0 * clamp(c[8], 0.0, 8.0)
    sharp = ce * (1 - s) + s * cu * max(ce, c0 * P.TYPES_CU_EDGE_MIN)
    puff = 1 - s + s * (cu + (wts * np.array(P.TYPES_PUFF)).sum(1)) if s > 0 else np.ones(len(cu))
    an = anvil.astype(np.float64)
    corners = [((1 - ax) * (1 - ay), an[y0, x0]), (ax * (1 - ay), an[y0, x0 + 1]),
               ((1 - ax) * ay, an[y0 + 1, x0]), (ax * ay, an[y0 + 1, x0 + 1])]
    cov = sum(b * a[:, 1] for b, a in corners)
    lev = sum(b * a[:, 1] * a[:, 0] for b, a in corners) / np.maximum(cov, 1e-6)
    tw = tower.astype(np.float64)
    corners = [((1 - ax) * (1 - ay), tw[y0, x0]), (ax * (1 - ay), tw[y0, x0 + 1]),
               ((1 - ax) * ay, tw[y0 + 1, x0]), (ax * ay, tw[y0 + 1, x0 + 1])]
    mark = sum(b * a[:, 0] for b, a in corners)
    mbase = sum(b * a[:, 0] * a[:, 1] for b, a in corners) / np.maximum(mark, 1e-6)
    if s <= 0:
        cov, mark = np.zeros(len(cov)), np.zeros(len(mark))
    return T, m, sharp, puff, cov, lev, mark, mbase


def soft_output(ir, quarter):
    """The soft transmittance's result: the end of the single-use chain that
    starts at the quarter value."""
    v = quarter
    for _ in range(6):
        users = [ir.def_name(i) for i in ir.users(v) if P.DEFINE.match(ir.lines[i])]
        if len(users) != 1:
            return None
        v = users[0]
    return v


def check_untile(stock: str, patched: str, failures: list) -> float:
    """The erosion noise's untiling (patch_dxil.apply_cloud_untile): each lookup's x and z
    coordinates are (position - Global[51] + offset) * scale, the offset from the Earth-fixed
    position (Global[110..112]) by UNTILE_WAVES, against float64; exactly stock with C0 = 0.
    Returns the worst error in metres."""
    groups = P.find_noise_lookups(P.Ir(stock))
    rng = np.random.default_rng(7)
    G = np.zeros((300, 4))
    G[51] = (-3712.5, 52113.0, 1287.25, 307200.0)
    # a planet frame from a capture near EKCH (world -> unit sphere, the camera in .w)
    G[110] = (1.308447e-07, 8.637934e-08, -5.639204e-12, 0.5522037)
    G[111] = (-1.279492e-08, 1.937121e-08, -1.550573e-07, 0.1238358)
    G[112] = (-8.600219e-08, 1.302749e-07, 2.337183e-08, 0.8272431)
    seeds = {}
    pos = {}
    for g in groups:
        xyz = np.stack([rng.uniform(-200e3, 200e3, N), rng.uniform(-10e3, 5e3, N), rng.uniform(-200e3, 200e3, N)], -1)
        for i in range(3):
            seeds[g["pos"][i]] = xyz[:, i].astype(F)
        pos[g["w"]] = xyz.astype(F).astype(np.float64)
    worst = 0.0
    for c0 in (0.0, 1.0):
        cloud = np.zeros((P.CLOUD_PARAMS_SIZE // 16, 4))
        cloud.flat[0] = c0
        cbs = {4800: G.astype(F), "b1 space50": cloud, "b0 space50": np.zeros((4, 4))}
        got, want = Evaluator(patched, cbs, seeds), Evaluator(stock, cbs, seeds)
        for g in groups:
            p = pos[g["w"]]
            planet = np.stack([p @ G[110 + r][:3] + G[110 + r][3] for r in range(3)], -1)
            off = {0: 0.0, 2: 0.0}
            for axis, amp, wave, direc, phase in P.UNTILE_WAVES:
                k = np.array(direc) / np.linalg.norm(direc) * P.EARTH_RADIUS_M / wave
                off[axis] = off[axis] + amp * np.sin(2 * np.pi * ((planet @ k + phase) % 1.0))
            for c in g["coords"]:
                scale = float(P.literal_value(c[3]))
                for axis in (0, 1, 2):
                    value = got(c[axis]).astype(np.float64)
                    if c0 <= 0 or axis == 1:
                        if not np.array_equal(value.astype(F), want(c[axis])):
                            failures.append(f"untile {c[axis]}: {'C0 = 0' if c0 <= 0 else 'y'} but not stock")
                        continue
                    ref = (p[:, axis] - G[51][axis] + off[axis]) * scale
                    err = float(np.abs(value - ref).max()) / scale
                    worst = max(worst, err)
                    if err > 0.5:
                        failures.append(f"untile {c[axis]}: off by {err:.2f} m")
    return worst


def check(crc: str, stock: str) -> list[str]:
    ir = P.Ir(stock)
    blends = P.find_phase_blends(ir)
    ms = P.find_cloud_ms(ir)
    edges = P.find_cloud_edges(ir)
    patched, summary = P.patch_clouds(stock)
    theta = np.linspace(0.0, math.pi, N)
    cos = np.cos(theta).astype(F)
    c64 = cos.astype(np.float64)
    rng = np.random.default_rng(3)
    s = rng.uniform(0, 1, N).astype(F)
    s[:200] = 1.0
    s[200:400] = 0.0
    sums = [n for n in (ir.def_name(i) for i in ir.users(ms["base"]) if P.DEFINE.match(ir.lines[i]))
            if ir.binops.get(n, ("",))[0] == "fadd"]
    rain = {[op for op in ir.binops[n][1:] if op != ms["base"]][0]: rng.uniform(-0.02, 0.05, N).astype(F) for n in sums}
    d = np.concatenate([np.linspace(0, 2, N // 2), np.linspace(2, 60, N - N // 2)]).astype(F)
    depth = rng.uniform(-100, 800, N).astype(F)  # m into the cloud
    # view and sun directions with dot(view, sun) = cos (seeded as such), sample distances
    sun = np.array([math.cos(math.radians(25)) * math.sin(math.radians(40)), math.sin(math.radians(25)),
                    math.cos(math.radians(25)) * math.cos(math.radians(40))])
    side = np.cross(sun, [0.3, 0.2, 0.9])
    side /= np.linalg.norm(side)
    view = np.cos(theta)[:, None] * sun + np.sin(theta)[:, None] * side
    dist = rng.uniform(100, 4000, N)
    cos_ops = ir.dot3[ms["cos"]]
    if all(light_reg(ir, v) for v in cos_ops[:3]):
        dir_names, light_names = cos_ops[3:], cos_ops[:3]
    else:
        dir_names, light_names = cos_ops[:3], cos_ops[3:]
    t_name = P.find_ap_depth(ir)
    ramp_x = np.linspace(-1.0, 2.0, N).astype(F)
    # the view march's step (m): its ramp is sharpened no steeper than max(160, step) / step,
    # and its extinction is at most OPTICAL_STEP_TAU per step
    _, step_name = P.view_march_step(ir, edges["ramps"][0][0])
    step = np.concatenate([[20.0, 160.0, 400.0], rng.uniform(15, 450, N - 3)]).astype(F)
    den = P.Ir(stock).lines[P.Ir(stock).defs[P.view_march_step(ir, edges["ramps"][0][0])[0]]]
    floor = float(P.literal_value(re.search(r"float ([0-9.e+-]+|0x[0-9A-F]+)[,)]", den)[1]))
    q = np.maximum(floor, step.astype(np.float64)) / step.astype(np.float64)
    cap = P.OPTICAL_STEP_TAU / step.astype(np.float64)
    softs = []
    for x, quarter in ms["softs"]:
        out = soft_output(ir, quarter)
        if out is None:
            return [f"soft transmittance of {x}: output not found"]
        softs.append((x, out))

    opt = P.find_view_extinction(ir)
    detail = P.find_detail_sites(ir)
    dseeds = {}
    for site in detail:
        dseeds[site["A"]] = rng.uniform(-0.75, 0.25, N).astype(F)
        rows = np.zeros((N, 4), F)
        rows[:, 1] = rng.uniform(0.5, 0.9, N)
        rows[:16, 1] = 0.5  # B = 0: at the base
        dseeds[site["vol"]] = rows
        dseeds[site["h"]] = rng.uniform(0, 6000, N).astype(F)
    if detail:
        vsite = detail[0]
        s1 = [o for o in P.Ir(stock).binops[vsite["s2"]][1:] if o != vsite["x"]][0]
        dseeds[s1] = rng.uniform(-0.05, 0.05, N).astype(F)
        for c in vsite["coords"]:
            dseeds[c] = rng.uniform(-30, 30, N).astype(F)
        dseeds[vsite["lod"]] = np.zeros(N, F)
    sigma = np.concatenate([[0.0, 1.5e-6, 1e-4, 0.01, 0.02], rng.uniform(0, 0.015, N - 5) ** 1.5 * 8]).astype(F)
    height = rng.uniform(-200, 15000, N).astype(F)
    for site in detail:  # the view march's altitude is the optical patch's too
        if site["h"] == opt["height"]:
            dseeds[site["h"]] = height
    # the view march's volume lookup, where the cloud types are read
    _, vu, vv, vw = P.find_volume_sample(ir, opt["height"], ir.defs[opt["prod"]])
    tu, tv = rng.uniform(-0.05, 1.05, N).astype(F), rng.uniform(-0.05, 1.05, N).astype(F)
    tw = np.concatenate([[0.0, 0.5, 31.5 / 32, 1.0, 1.2], rng.uniform(0, 1, N - 5)]).astype(F)
    dseeds.update({vu: tu, vv: tv, vw: tw})
    failures, worst = [], 0.0
    for c in SETS:
        c = tuple(c) + (0.0,) * (P.CLOUD_PARAMS_SIZE // 4 - len(c))
        cloud = np.zeros((P.CLOUD_PARAMS_SIZE // 16, 4))
        cloud.flat[:len(c)] = c
        cbs = {"b1 space50": cloud, "b0 space50": np.zeros((4, 4))}
        seeds = {ms["s"]: s, **rain, opt["sigma"]: sigma, opt["height"]: height, **dseeds, step_name: step}
        for _, cosname, *_ in blends:
            seeds[cosname] = cos
        for x, _ in softs:
            seeds[x] = d
        seeds[edges["depth"]] = depth
        for k3 in range(3):
            seeds[dir_names[k3]] = view[:, k3].astype(F)
            seeds[light_names[k3]] = np.full(N, sun[k3], dtype=F)
        seeds[t_name] = dist.astype(F)
        for x, _ in edges["ramps"]:
            seeds[x] = ramp_x
        got = Evaluator(patched, cbs, {**seeds, LV: np.ones(N, F)}, sampler=fine_sampler)
        want = Evaluator(stock, cbs, seeds)
        c0 = clamp(c[0], 0.0, 1.0)
        w = c0 * clamp(c[2], 0.0, 0.5)
        k = c0 * clamp(c[3], 0.0, 1.0)
        g_ms = c0 * clamp(c[4], 0.0, 0.9)
        floor_on = c[5] > 0 and c0 > 0
        powder = c0 * clamp(c[6], 0.0, 1.0)
        build_up = max(c[7], 1.0) if c[7] > 0 else 100.0
        sharp = c0 * clamp(c[8], 0.0, 8.0)
        glory = c0 * max(c[9], 0.0)
        radius = clamp(c[10], 1.0, 50.0) if c[10] > 0 else 10.0
        spread = clamp(c[11], 0.01, 0.5) if c[11] > 0 else 0.15
        gamma = np.sqrt(2 * np.maximum(1 + c64, 0))
        channels = [0, 1, 2] if len(ms["adds"]) == 3 else [1]
        tag = f"C={c}"

        # forward lobe
        for name, cosname, forward, g_fwd, g_back in blends:
            value = got(name).astype(np.float64)
            if w <= 0:
                if not np.array_equal(value.astype(F), want(name)):
                    failures.append(f"{tag} {name}: forward lobe off but not stock")
                continue
            g = clamp(c[1], 0.0, 0.99)
            ref = 0.5 * hg(g_back, c64) + (0.5 - w) * hg(g_fwd, c64) + w * hg(g, c64)
            worst = max(worst, compare(value, ref, 2e-3 if g > 0.95 else 1e-4, 1e-2 if g > 0.95 else 1e-3,
                                       f"{tag} {name}", failures))

        # sun phase with multiple scattering, per channel
        blend = got(ms["blend"]).astype(np.float64)
        p_ms = hg(g_ms, c64) if g_ms > 0 else np.full(N, float(P.literal_value(P.INV_4PI)))
        s64 = s.astype(np.float64)
        for (y, ry), add, ch in zip(rain.items(), ms["adds"], channels):
            value = got(add).astype(np.float64)
            if w <= 0 and k <= 0 and g_ms <= 0 and powder <= 0 and glory <= 0 and shadow_off(c):
                if not np.array_equal(value.astype(F), want(add)):
                    failures.append(f"{tag} {add}: multiple scattering and powder off but not stock")
                continue
            v = ((1 - c64) / 2) ** 2
            f = 1 - powder * v * np.exp(-np.maximum(depth.astype(np.float64), 0) / build_up)
            z = 2 * math.pi * radius / P.GLORY_WAVELENGTHS[ch] * gamma
            rings = (1 + np.exp(-2 * (spread * z) ** 2) * (1 - np.exp(-P.GLORY_FIT_M * z * z))
                     * np.sin(2 * z + P.GLORY_FIT_PHASE)) / (math.pi * z / 2 + np.exp(-P.GLORY_FIT_K * z))
            ref = ((blend + ry - p_ms) * s64 * s64 * (1 - k) + p_ms) * f + 0.1 * glory * rings * s64 * s64 * (1 - k)
            ref = ref * shadow_ref(view, sun, dist, c, s64)
            # inputs of either sign: compare against the phase's scale, not 0
            err = np.nan_to_num(np.abs(value - ref) / np.maximum(np.abs(ref), 0.01), nan=np.inf)
            e99 = float(np.percentile(err, 99.9))
            worst = max(worst, e99)
            if e99 > 2e-3 or err.max() > 1e-2:
                failures.append(f"{tag} {add}: rel. error p99.9 {e99:.1e}, max {err.max():.1e}")

        # soft transmittance
        a = 0.6 - math.log(clamp(c[5], 0.001, 0.9)) if floor_on else 4.0
        d64 = d.astype(np.float64)
        ref = np.maximum(0.0, np.exp(0.6 - d64 / (1 + d64 / a)))
        for x, out in softs:
            value = got(out).astype(np.float64)
            if not floor_on:
                if not np.array_equal(value.astype(F), want(out)):
                    failures.append(f"{tag} {out}: floor off but not stock")
                continue
            worst = max(worst, compare(value, ref, 1e-5, 1e-4, f"{tag} {out}", failures))

        # boundary ramps
        x64 = ramp_x.astype(np.float64)
        stock_ramp = np.clip(1.5 - np.exp2(x64 * P.literal_value(P.EDGE_SLOPE)), 0, 1)
        for r, (x, ramp) in enumerate(edges["ramps"]):
            value = got(ramp).astype(np.float64)
            if sharp <= 0:
                if not np.array_equal(value.astype(F), want(ramp)):
                    failures.append(f"{tag} {ramp}: edges off but not stock")
                continue
            slope = np.minimum(1 + sharp, q) if r == 0 else 1 + sharp
            ref = np.clip((stock_ramp - 0.5) * slope + 0.5, 0, 1)
            err = float(np.nan_to_num(np.abs(value - ref), nan=np.inf).max())
            if err > 1e-5:
                failures.append(f"{tag} {ramp}: abs. error max {err:.1e}")

        # optical thickness: the view march's ramp * sigma'
        prod = got(opt["prod"]).astype(np.float64)
        ramp_v = got(edges["ramps"][0][1])
        if c[16] <= 0 or c0 <= 0:
            # off: exactly the (possibly C8-sharpened) ramp times MSFS's extinction
            if not np.array_equal(prod.astype(F), (ramp_v * sigma).astype(F)):
                failures.append(f"{tag} {opt['prod']}: optical thickness off but not stock")
            if c[8] <= 0 and not np.array_equal(prod.astype(F), want(opt["prod"])):
                failures.append(f"{tag} {opt['prod']}: optical thickness and edges off but not stock")
        else:
            s64, h64 = sigma.astype(np.float64), height.astype(np.float64)
            gam = clamp(c[17], 0.2, 1.0) if c[17] > 0 else 1.0
            liquid = clamp(c[16], 0.0, 20.0) * P.OPTICAL_REF * (s64 / P.OPTICAL_REF) ** gam
            ice = (clamp(c[19], 0.0, 20.0) if c[19] > 0 else 1.0) * s64
            h_ice = c[18] if c[18] > 0 else P.OPTICAL_ICE_DEFAULT_M
            wi = np.clip((h64 - h_ice + P.OPTICAL_ICE_BLEND_M) / P.OPTICAL_ICE_BLEND_M, 0, 1)
            ref = ramp_v.astype(np.float64) * np.minimum(liquid + wi * (ice - liquid), cap)
            # relative to 1e-5/m at least (products near a cloud's edge are ~0), and to the liquid
            # term's float32 resolution: the ice blend is a lerp, so near the ice side its error
            # is that of the (much larger) liquid value
            floor = np.maximum(1e-5, 1e-3 * np.abs(ramp_v.astype(np.float64) * liquid))
            err = np.abs(prod - ref) / np.maximum(np.abs(ref), floor)
            e = float(np.nan_to_num(err, nan=np.inf).max())
            worst = max(worst, float(np.percentile(err, 99.9)))
            if e > 1e-4:
                failures.append(f"{tag} {opt['prod']}: optical thickness rel. error max {e:.1e}")

        # surface detail: every march's 1 km octave, and the view march's finer one
        k28 = c0 * clamp(c[28], 0.0, 1.0)
        k30 = c0 * clamp(c[30], 0.0, 0.1)
        depth1 = max(c[29], 10.0) if c[29] > 0 else P.DETAIL_BASE_DEPTH_M
        period = clamp(c[31], 50.0, 1000.0) if c[31] > 0 else P.DETAIL_FINE_PERIOD_M
        for i, site in enumerate(detail):
            x_val = got(site["x"]).astype(np.float64)
            a = np.ones(N)
            if k28 <= 0:
                if not np.array_equal(x_val.astype(F), want(site["x"])):
                    failures.append(f"{tag} {site['x']}: base smoothing off but not stock")
            else:
                B = (2 * dseeds[site["vol"]][:, 1].astype(np.float64) - 1) ** 2
                r = np.minimum(0.5 * B * np.exp(dseeds[site["h"]].astype(np.float64) / P.PROFILE_SCALE_M), 0.99)
                D = P.PROFILE_GROWTH_M * np.sqrt(np.maximum(-np.log(1 - r), 0))
                t = np.clip(D / depth1, 0, 1)
                a = 1 + k28 * (t * t * (3 - 2 * t) - 1)
                ref = dseeds[site["A"]].astype(np.float64) * 0.05 * a
                err = float(np.abs(x_val - ref).max())
                if err > 2e-5:
                    failures.append(f"{tag} {site['x']}: base smoothing abs. error max {err:.1e}")
            if i == 0:
                s2_val = got(site["s2"]).astype(np.float64)
                if k30 <= 0:
                    if k28 <= 0 and not np.array_equal(s2_val.astype(F), want(site["s2"])):
                        failures.append(f"{tag} {site['s2']}: detail off but not stock")
                    continue
                f = 1000.0 / period
                cs = [dseeds[cn].astype(np.float64) * f for cn in site["coords"]]
                n3 = 0.5 + 0.45 * np.sin(7.3 * cs[0] + 3.1 * cs[1] + 5.7 * cs[2])
                ref = got("%mod.fo.s2").astype(np.float64) + k30 * a * (n3 - 0.75)
                err = float(np.abs(s2_val - ref).max())
                if err > 2e-5:
                    failures.append(f"{tag} {site['s2']}: finer octave abs. error max {err:.1e}")
    # cloud types: the view ramp and the optical thickness by the grid's weights
    puff_products = [(e, P.Ir(patched).def_name(i)) for i, l in enumerate(patched.splitlines())
                     for e in ("%mod.ty.e8", "%mod.ty.e1") if l.endswith(f"= fmul fast float {e}, %mod.ty.pf")]
    if not any(e == "%mod.ty.e8" for e, _ in puff_products):
        failures.append("types: the view march's 8 km erosion octave isn't scaled by the puff factor")
    cirrus = {}
    for i, l in enumerate(patched.splitlines()):
        if l.endswith("= select i1 %mod.ci.on, float %mod.ci.alt, float %mod.ci.a0"):
            cirrus["alt"] = P.Ir(patched).def_name(i)
        if l.endswith("= call float @dx.op.binary.f32(i32 35, float %mod.ci.c0, float %mod.ci.cov)"):
            cirrus["cov"] = P.Ir(patched).def_name(i)
    band = "no cirrus band" not in summary  # (20 variants have no cirrus band)
    if band and sorted(cirrus) != ["alt", "cov"]:
        failures.append(f"types: the cirrus band isn't linked to the anvils ({sorted(cirrus)})")
    # the storm towers' sky light: the light volume's sums of the ambient's visibilities (stock names)
    lv_sums = P.find_light_volume_sums(ir)
    if not lv_sums or "%mod.sa.lf = " not in patched:
        failures.append("types: the towers' sky light isn't on the light volume")
    words, grid = types_buffer(rng)
    bad_magic, _ = types_buffer(np.random.default_rng(3), magic=P.TYPES_MAGIC ^ 1)
    ramp0 = edges["ramps"][0][1]
    x64 = ramp_x.astype(np.float64)
    stock_ramp = np.clip(1.5 - np.exp2(x64 * P.literal_value(P.EDGE_SLOPE)), 0, 1)
    for c in TYPE_SETS:
        cloud = np.zeros((P.CLOUD_PARAMS_SIZE // 16, 4))
        cloud.flat[:len(c)] = c
        mod = np.zeros((4, 4))
        mod[0, 0] = np.uint32(P.DEBUG_BUFFER_TYPES).view(np.float32)
        untyped = list(c)
        untyped[23] = 0.0
        plain = np.zeros((P.CLOUD_PARAMS_SIZE // 16, 4))
        plain.flat[:len(c)] = untyped
        seeds = {ms["s"]: s, **rain, opt["sigma"]: sigma, opt["height"]: height, **dseeds, LV: np.ones(N, F),
                 edges["depth"]: depth, t_name: dist.astype(F), step_name: step}
        for _, cosname, *_ in blends:
            seeds[cosname] = cos
        for x, _ in softs:
            seeds[x] = d
        for k3 in range(3):
            seeds[dir_names[k3]] = view[:, k3].astype(F)
            seeds[light_names[k3]] = np.full(N, sun[k3], dtype=F)
        for x, _ in edges["ramps"]:
            seeds[x] = ramp_x
        tag = f"types C={c}"
        for e_name, _ in puff_products:  # (the octaves themselves need the march's position)
            seeds[e_name] = rng.uniform(-0.2, 0.1, N).astype(F)
        seeds["%mod.ci.a0"] = np.full(N, 12000.0, F)  # (MSFS's band and cover: the stock cbuffer and t8)
        seeds["%mod.ci.c0"] = rng.uniform(0, 1, N).astype(F)
        for name in (lv_sums or ())[:2]:
            seeds[name] = rng.uniform(0, 2 * lv_sums[2], N).astype(F)  # (n taps, each up to ~1.82)
        base = Evaluator(patched, {"b1 space50": plain, "b0 space50": mod}, seeds, sampler=fine_sampler, buffer=words)
        want_prod, want_ramp = base(opt["prod"]), base(ramp0)
        # off: without bit 7, with a wrong magic, with C23 = 0: exactly as without types
        for what, flags, buf in (("no bit 7", 0, words), ("wrong magic", P.DEBUG_BUFFER_TYPES, bad_magic)):
            m2 = np.zeros((4, 4))
            m2[0, 0] = np.uint32(flags).view(np.float32)
            off = Evaluator(patched, {"b1 space50": cloud, "b0 space50": m2}, seeds, sampler=fine_sampler, buffer=buf)
            if not (np.array_equal(off(opt["prod"]), want_prod) and np.array_equal(off(ramp0), want_ramp)):
                failures.append(f"{tag}: types off ({what}) but not as without them")
        got = Evaluator(patched, {"b1 space50": cloud, "b0 space50": mod}, seeds, sampler=fine_sampler, buffer=words)
        T, m, sharp, puff, cov, lev, mark, mbase = types_ref(c, grid, tu, tv, tw)
        # the towers' sky light: the floor n CB_SIDE_AMBIENT mark sat((h - base) / ramp), and the
        # light volume's sums raised to it exactly
        if lv_sums:
            lf = lv_sums[2] * P.CB_SIDE_AMBIENT * mark * np.clip((height.astype(np.float64) - mbase)
                                                                  / P.CB_SIDE_RAMP_M, 0, 1)
            err = float(np.abs(got("%mod.sa.lf").astype(np.float64) - lf).max())
            if err > 1e-4:
                failures.append(f"{tag} %mod.sa.lf: towers' sky light abs. error max {err:.1e}")
            for i, name in enumerate(lv_sums[:2]):
                if not np.array_equal(got(f"%mod.sa.v{i}"), np.maximum(got(name), got("%mod.sa.lf"))):
                    failures.append(f"{tag} %mod.sa.v{i}: the light volume's {name} isn't raised to the floor")
        # the anvils' cover and level, and MSFS's cirrus band centred on it / covered as much
        got_cov = got("%mod.ci.cov").astype(np.float64)
        err = float(np.abs(got_cov - cov).max())
        if err > 2e-5:
            failures.append(f"{tag} %mod.ci.cov: anvil cover abs. error max {err:.1e}")
        on = cov > 0.02
        err = float((np.abs(got("%mod.ci.alt").astype(np.float64) - lev) / lev)[on].max()) if on.any() else 0.0
        if err > 1e-5:
            failures.append(f"{tag} %mod.ci.alt: anvil level rel. error max {err:.1e}")
        ci_on = got_cov > P.CI_MIN_COV
        if band and sorted(cirrus) == ["alt", "cov"]:
            if not np.array_equal(got(cirrus["alt"]), np.where(ci_on, got("%mod.ci.alt"), got("%mod.ci.a0"))):
                failures.append(f"{tag} {cirrus['alt']}: the cirrus band isn't centred on the anvils")
            if not np.array_equal(got(cirrus["cov"]), np.maximum(got("%mod.ci.c0"), got("%mod.ci.cov"))):
                failures.append(f"{tag} {cirrus['cov']}: the cirrus cover isn't raised to the anvils'")
        # the puffs: the factor, and the view march's two erosion octaves scaled by it exactly
        err = float(np.abs(got("%mod.ty.pf").astype(np.float64) - puff).max())
        if err > 2e-5:
            failures.append(f"{tag} %mod.ty.pf: puff factor abs. error max {err:.1e}")
        for e_name, out_name in puff_products:
            if not np.array_equal(got(out_name), (got(e_name) * got("%mod.ty.pf")).astype(F)):
                failures.append(f"{tag} {out_name}: the erosion octave isn't scaled by the puff factor")
        ref_ramp = np.where(sharp <= 0, stock_ramp,
                            np.clip((stock_ramp - 0.5) * np.minimum(1 + sharp, q) + 0.5, 0, 1))
        err = float(np.nan_to_num(np.abs(got(ramp0).astype(np.float64) - ref_ramp), nan=np.inf).max())
        if err > 2e-5:
            failures.append(f"{tag} {ramp0}: typed view ramp abs. error max {err:.1e}")
        c0 = clamp(c[0], 0.0, 1.0)
        s64, h64 = sigma.astype(np.float64), height.astype(np.float64)
        if c[16] <= 0 or c0 <= 0:
            liquid = s64
            ice_w = np.zeros(N)
            ice = s64
        else:
            gam = clamp(c[17], 0.2, 1.0) if c[17] > 0 else 1.0
            k16 = clamp(c[16], 0.0, 20.0) * P.OPTICAL_REF
            plain_l = k16 * (s64 / P.OPTICAL_REF) ** gam
            typed = k16 * T * (s64 / P.OPTICAL_REF) ** (gam + m * (1 - gam))
            liquid = np.maximum(typed, np.minimum(plain_l, s64))
            ice = (clamp(c[19], 0.0, 20.0) if c[19] > 0 else 1.0) * s64
            h_ice = c[18] if c[18] > 0 else P.OPTICAL_ICE_DEFAULT_M
            ice_w = np.clip((h64 - h_ice + P.OPTICAL_ICE_BLEND_M) / P.OPTICAL_ICE_BLEND_M, 0, 1)
        # (against the shader's ramp, checked above: sharpening by up to 9 amplifies its rounding)
        ramp_got = got(ramp0).astype(np.float64)
        blend = liquid + ice_w * (ice - liquid)
        ref = ramp_got * (blend if c[16] <= 0 or c0 <= 0 else np.minimum(blend, cap))
        floor = np.maximum(1e-5, 1e-3 * np.abs(ramp_got * liquid))
        err = np.abs(got(opt["prod"]).astype(np.float64) - ref) / np.maximum(np.abs(ref), floor)
        e = float(np.nan_to_num(err, nan=np.inf).max())
        worst = max(worst, float(np.percentile(err, 99.9)))
        if e > 1e-4:
            failures.append(f"{tag} {opt['prod']}: typed optical thickness rel. error max {e:.1e}")
    worst_shadow = check_shadow(patched, dir_names, light_names, t_name, ms["s"], failures)
    worst_shadow = max(worst_shadow, check_shadow_map(patched, dir_names, light_names, t_name, ms["s"], failures))
    check_light_volume(stock, patched, failures)
    worst_untile = check_untile(stock, patched, failures)
    print(f"{'FAIL' if failures else 'PASS'} {crc}: {len(blends)} phase blend(s), sun phase {ms['blend']}, "
          f"{len(softs)} soft transmittance(s), {len(edges['ramps'])} edge ramp(s); "
          f"worst p99.9 rel. error {worst:.1e}; shadow max abs. error {worst_shadow:.1e}; "
          f"untiling max error {worst_untile:.2f} m")
    return failures


def main():
    dxc = P.find_dxc()
    rows = [r for r in csv.DictReader(open(ROOT / "extracted" / "index.csv"))
            if r["entry"] == "ch_volumetric_clouds_render"]
    wanted = set(sys.argv[1:])
    failed = 0
    for row in rows:
        crc = Path(row["path"]).name.split(".")[0]
        if wanted and crc not in wanted:
            continue
        try:
            problems = check(crc, P.disassemble(ROOT / "extracted" / row["path"], dxc))
        except P.PatchError as e:
            print(f"FAIL {crc}: not patched: {e}")
            problems = [str(e)]
        for p in problems[:8]:
            print("   ", p)
        failed += bool(problems)
        sys.stdout.flush()
    if failed:
        raise SystemExit(f"{failed} shader(s) failed")


if __name__ == "__main__":
    main()

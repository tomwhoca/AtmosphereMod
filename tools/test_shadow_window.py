"""Offline test of the aircraft's live shadow window (patch_dxil.silhouette_lines).

Runs the patched ch_deferred_shadows IR for every thread of the window build
(vectorised) against a synthetic cascaded shadow map with known matrices, and
checks against numpy:
- the cascade it picks (the finest that holds the window's A around the camera);
- the coverage pyramid (levels 0-6) cell by cell, including the band that
  keeps far casters (the ground) out;
- the header: the world -> cell and world -> depth rows (through the
  world -> view rotation Global[134..136]), c0, dpm, the camera's depth;
- the flag ((frame & 0xFFFF) | 0x10000 when a caster is in the window, else not written).

    python tools/test_shadow_window.py
"""

import csv
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import patch_dxil as P  # noqa: E402
from test_medium_patch import Evaluator, F  # noqa: E402

SIZE = 1536
COVER = (4.0, 20.0, 202.0, 2429.0)  # m per cascade
DPM = 1.0 / 2429.0                    # depth per m (all cascades)
FRAME = 0x1234A


def rotation(rng):
    q, _ = np.linalg.qr(rng.normal(size=(3, 3)))
    return q * np.sign(np.linalg.det(q))


def scene(rng, cam_uv):
    """World -> view rotation R, the sun (world), cascade rows in view space."""
    R = rotation(rng)                       # rows: world -> view
    sun_w = np.array([0.3, 0.8, -0.52])
    sun_w /= np.linalg.norm(sun_w)
    L = R @ sun_w                           # in view space
    e_u = np.cross(L, [0.0, 1.0, 0.0])
    e_u /= np.linalg.norm(e_u)
    e_v = np.cross(L, e_u)
    rows = np.zeros((43, 4))
    for k, cover in enumerate(COVER):
        rows[5 * k, :3], rows[5 * k, 3] = e_u / cover, cam_uv[k][0]
        rows[5 * k + 1, :3], rows[5 * k + 1, 3] = e_v / cover, cam_uv[k][1]
        rows[5 * k + 2, :3], rows[5 * k + 2, 3] = L * DPM, 0.79
        rows[5 * k + 3] = (0, 0, 0, 1)
    rows[40] = (SIZE, 1.0 / SIZE, 1.0 / SIZE, SIZE)
    rows[42, 0] = np.array([4], np.int32).view(np.float32)[0]
    return R, sun_w, rows


def aircraft_depth(rows, R, k, x, y):
    """The synthetic map, slice k at texels (x, y): an aircraft-like cross of
    casters around the camera (up to 3 m toward the sun), 'ground' 300 m
    down-sun of the camera in a stripe (outside the band), and a building 30 m
    off-band in the cockpit case; 0 (empty) elsewhere."""
    u, v, d = rows[5 * k], rows[5 * k + 1], rows[5 * k + 2]
    upm = np.linalg.norm(u[:3])
    # texel -> metres in the light plane, relative to the camera
    mu = ((x + 0.5) / SIZE - u[3]) / upm
    mv = ((y + 0.5) / SIZE - v[3]) / upm
    fuselage = (np.abs(mv - 2.0) < 2.0) & (mu > -36) & (mu < 2)
    wing = (np.abs(mu + 15) < 3.5) & (np.abs(mv - 2.0) < 17)
    depth = np.where(fuselage | wing, d[3] + DPM * (1.0 + 0.05 * mu), 0.0)
    ground = (mv > 25) & (mv < 60)
    depth = np.where((depth == 0) & ground, d[3] - DPM * 300.0, depth)
    return depth


def run(rng, c15, cam_uv, frame=FRAME, rt_on=False, rt_bound=False):
    blob = sorted((ROOT / "extracted" / "blobs" / "ch_deferred_shadows").glob("*.cso"))[0]
    text, _ = P.patch_shadow_capture(P.disassemble(blob, P.find_dxc()))
    R, sun_w, rows = scene(rng, cam_uv)
    glob = np.zeros((300, 4))
    glob[134:137, :3] = R
    mod = np.zeros((4, 4))
    flags = (frame & 0xFFFF) << 16 | 16 | 4 | 8 | (32 if rt_bound else 0)
    mod[0, 0] = np.array([flags], np.uint32).view(np.float32)[0]
    cloud = np.zeros((4, 4))
    cloud[3, 3] = c15
    cbs = {"b0 space50": mod, "b1 space50": cloud, 720: rows, 4800: glob}

    def texture(x, y, k):
        x, y, k = np.broadcast_arrays(np.asarray(x), np.asarray(y), np.asarray(k))
        out = np.zeros(x.shape + (4,), F)
        if not rt_on:
            for kk in range(4):
                sel = k == kk
                out[sel, 0] = aircraft_depth(rows, R, kk, x[sel], y[sel])
        return out

    tids = re.findall(r"^\s+(%[\w.]+) = call i32 @dx\.op\.threadId\.i32\(i32 93, i32 ([01])\)", text, re.M)
    ys, xs = np.mgrid[0:256, 0:512]
    seeds = {n: (xs.ravel() if c == "0" else ys.ravel()).astype(np.int64) for n, c in tids}
    if not rt_bound:
        seeds.update({f"%mod.sl.{tag}rc": np.float32(0.0) for tag in ("l0", "ln")})
    ev = Evaluator(text, cbs, seeds, texture=texture)
    return text, ev, R, sun_w, rows, xs.ravel(), ys.ravel()


def reference(rows, R, c15, k, rt_on=False):
    span = abs(c15)
    w = np.clip(P.SIL_SPAN_FACTOR * span, P.SIL_MIN_M, P.SIL_MAX_M) if c15 > 0 else P.SIL_EXTERNAL_M
    a = w / 2 if c15 > 0 else P.SIL_EXTERNAL_A_M
    u, v, d = rows[5 * k], rows[5 * k + 1], rows[5 * k + 2]
    upm, vpm, dpm = (np.linalg.norm(r[:3]) for r in (u, v, d))
    c0 = w / P.SIL_N0
    cu, cv = (u[3] - w / 2 * upm) * SIZE, (v[3] - w / 2 * vpm) * SIZE
    tu, tv = c0 * upm * SIZE, c0 * vpm * SIZE
    band = (a + P.SIL_BAND_MARGIN_M) * dpm
    levels = []
    for L in range(P.SIL_LEVELS):
        n = P.SIL_N0 >> L
        taps = P.SIL_TAPS0 if L == 0 else P.SIL_TAPS
        j, i = np.mgrid[0:n, 0:n]
        cov = np.zeros((n, n))
        for p in range(taps):
            for q in range(taps):
                tx = cu + tu * 2 ** L * (i + (p + 0.5) / taps)
                ty = cv + tv * 2 ** L * (j + (q + 0.5) / taps)
                inside = (tx >= 0) & (tx < SIZE) & (ty >= 0) & (ty < SIZE)
                dd = aircraft_depth(rows, R, k, np.trunc(np.clip(tx, 0, SIZE - 1)), np.trunc(np.clip(ty, 0, SIZE - 1)))
                if rt_on:
                    dd = np.zeros_like(dd)
                cov += inside & (dd > 0) & (np.abs(dd - d[3]) < band)
        cov[0, :] = cov[-1, :] = cov[:, 0] = cov[:, -1] = 0  # the outer ring
        levels.append(cov / taps ** 2)
    U = (u[:3] @ R) / (c0 * upm)
    V = (v[:3] @ R) / (c0 * vpm)
    D = d[:3] @ R
    return {"w": w, "a": a, "c0": c0, "dpm": dpm, "dcam": d[3], "U": U, "V": V, "D": D, "levels": levels,
            "cu": cu, "cv": cv, "tu": tu, "tv": tv}


def stores(text):
    """Constant-offset header stores: offset -> the four value names."""
    out = {}
    for m in re.finditer(r"rawBufferStore\.f32\(i32 140, %dx\.types\.Handle %mod\.sc\.u, i32 (\d+), i32 undef, "
                         r"float (\S+), float (\S+), float (\S+), float (\S+), i8 \d+", text):
        out[int(m[1])] = [m[k] for k in range(2, 6)]
    return out


def check_case(label, rng, c15, cam_uv, want_k, failures, rt_on=False):
    text, ev, R, sun_w, rows, xs, ys = run(rng, c15, cam_uv, rt_on=rt_on)
    go = np.asarray(ev("%mod.sl.go"), bool)
    is0 = np.asarray(ev("%mod.sl.is0"), bool) & go
    isn = np.asarray(ev("%mod.sl.isn"), bool) & go
    k = int(np.asarray(ev("%mod.sl.k0")).ravel()[0])
    if k != want_k:
        failures.append(f"{label}: cascade {k}, want {want_k}")
        return
    ref = reference(rows, R, c15, k, rt_on)
    # the pyramid, as the threads store it
    mem = {}
    a0 = np.asarray(ev("%mod.sl.a.o"))[is0]
    v0 = np.asarray(ev("%mod.sl.av"))[is0]
    an = np.asarray(ev("%mod.sl.b.o"))[isn]
    vn = np.asarray(ev("%mod.sl.bv"))[isn]
    for addr, val in zip(np.concatenate([a0, an]), np.concatenate([v0, vn])):
        mem[int(addr)] = float(val)
    expected_cells = sum((P.SIL_N0 >> L) ** 2 for L in range(P.SIL_LEVELS))
    if len(mem) != expected_cells or len(a0) + len(an) != expected_cells:
        failures.append(f"{label}: {len(a0) + len(an)} stores to {len(mem)} cells, want {expected_cells}")
    worst = 0.0
    for L in range(P.SIL_LEVELS):
        n = P.SIL_N0 >> L
        base = P.SIL_REGION + P.SIL_OFFSETS[L]
        got = np.array([mem.get(base + 4 * idx, np.nan) for idx in range(n * n)]).reshape(n, n)
        if np.isnan(got).any():
            failures.append(f"{label}: level {L}: {int(np.isnan(got).sum())} cells not written")
            continue
        worst = max(worst, float(np.abs(got - ref["levels"][L]).max()))
    if worst > 1e-6:
        failures.append(f"{label}: coverage off by up to {worst:.3g}")
    # independently of the band formula: the ground stripe (300 m down-sun,
    # 25-60 m to the side in v) must not count as the aircraft
    n0 = P.SIL_N0
    mv = (np.arange(n0) + 0.5 - n0 / 2) * ref["c0"]
    stripe = (mv > 27) & (mv < 58)
    level0 = np.array([mem.get(P.SIL_REGION + P.SIL_OFFSETS[0] + 4 * idx, 0.0) for idx in range(n0 * n0)])
    if stripe.any() and level0.reshape(n0, n0)[stripe, :].max() > 0:
        failures.append(f"{label}: the ground stripe counts as the aircraft")
    total0 = ref["levels"][0].sum()
    if not rt_on and total0 < 50:
        failures.append(f"{label}: the reference window holds almost no aircraft ({total0:.0f} cells)")
    # header (thread 0)
    st = stores(text)
    first = np.flatnonzero((xs == 0) & (ys == 0))[0]

    def one(n):
        if n == "undef":
            return float("nan")
        value = np.asarray(ev(n) if n.startswith("%") else ev.val(n))
        return float(value.ravel()[first] if value.ndim else value)

    def h(off):
        return [one(n) for n in st[P.SIL_REGION + off]]
    c0, dpm, dcam, _ = h(4)
    U, V, D = h(16), h(32), h(48)
    for name, got, want, tol in (("c0", c0, ref["c0"], 1e-6), ("dpm", dpm, ref["dpm"], 1e-9),
                                 ("camera depth", dcam, ref["dcam"], 1e-7)):
        if abs(got - want) > tol * max(1.0, abs(want)):
            failures.append(f"{label}: header {name} {got} != {want}")
    for name, got, want in (("U", U, ref["U"]), ("V", V, ref["V"]), ("D", D, ref["D"])):
        if np.abs(np.array(got[:3]) - want).max() > 1e-4 * max(1.0, np.abs(want).max()):
            failures.append(f"{label}: header row {name} {np.round(got[:3], 6)} != {np.round(want, 6)}")
    if abs(U[3] - P.SIL_N0 / 2) > 0 or abs(V[3] - P.SIL_N0 / 2) > 0 or abs(D[3] - ref["dcam"]) > 1e-7:
        failures.append(f"{label}: header offsets {U[3]}, {V[3]}, {D[3]}")
    # geometry: a world point lands in the cell of its texel
    pw = rng.normal(size=(50, 3)) * 20
    pv = pw @ R.T
    u, v = rows[5 * k], rows[5 * k + 1]
    tx = (pv @ u[:3] + u[3]) * SIZE
    ty = (pv @ v[:3] + v[3]) * SIZE
    cell = np.stack([pw @ np.array(U[:3]) + U[3], pw @ np.array(V[:3]) + V[3]], 1)
    want = np.stack([(tx - ref["cu"]) / ref["tu"], (ty - ref["cv"]) / ref["tv"]], 1)
    if np.abs(cell - want).max() > 1e-3:
        failures.append(f"{label}: world -> cell off by {np.abs(cell - want).max():.3g} cells")
    # flag: written by the level-0 cells holding a caster
    any_cell = np.asarray(ev("%mod.sl.a.any"), bool)[is0]
    fv = int(np.asarray(ev("%mod.sl.fv")).ravel()[0])
    if fv != ((FRAME & 0xFFFF) | 0x10000):
        failures.append(f"{label}: flag value {fv:#x}")
    if rt_on == bool(any_cell.any()):
        failures.append(f"{label}: flag {'written' if any_cell.any() else 'not written'} with RT shadows "
                        f"{'on' if rt_on else 'off'}")
    print(f"{label}: cascade {k}, window {ref['w']:.1f} m (cell {ref['c0']:.3f} m), "
          f"{int(ref['levels'][0].astype(bool).sum())} level-0 cells covered, coverage max error {worst:.1e}")


def camera_sun_ref(glob, layer):
    """numpy reference for the sim's cloud-layer shadow at the camera, which
    the patch copies into the window header."""
    R, t = glob[134:137, :3], glob[134:137, 3]
    w = R.T @ (-t)
    X, Z, Y = glob[132, 0] + w[0], glob[132, 2] + w[2], w[1]
    fade = np.clip(4 * (X * X + Z * Z) * glob[92, 2] ** 2, 0, 1)
    u, v = (X - glob[92, 0]) * glob[92, 2] + 0.5, (Z - glob[92, 1]) * glob[92, 2] + 0.5
    lo, hi = layer(u, v)
    if abs(hi - lo) <= 1e-4:
        return 1.0
    alt = glob[132, 1] - lo + Y + max(0.0, glob[117, 1] - 1e4) * 0.01 + glob[117, 1]
    return float(np.clip(alt / (hi - lo), 0, 1) * (1 - fade) + fade)


def check_camera_sun(rng, failures):
    """The window header's sunlight at the camera (cloud_shadow_at_camera) in
    the variants with MSFS's cloud-layer shadow, against camera_sun_ref: below,
    inside and above a layer, and with no cloud; and the header's frame."""
    dxc = P.find_dxc()
    for blob in sorted((ROOT / "extracted" / "blobs" / "ch_deferred_shadows").glob("*.cso")):
        text, summary = P.patch_shadow_capture(P.disassemble(blob, dxc))
        if "from the cloud-layer shadow" not in summary:
            continue
        for label, altitude, bottom, top in (("below the layer", 900.0, 1200.0, 2000.0),
                                             ("inside it", 1500.0, 1200.0, 2000.0),
                                             ("above it", 2600.0, 1200.0, 2000.0),
                                             ("no cloud", 1500.0, 0.0, 0.0)):
            glob = np.zeros((300, 4))
            R = rotation(rng)
            glob[134:137, :3] = R
            glob[134:137, 3] = rng.normal(size=3) * 50
            glob[132] = (3.0, 0.0, -7.0, 0.0)
            glob[92] = (10.0, -20.0, 1 / 20000.0, 0.0)
            glob[117, 1] = altitude

            def layer(u, v, b=bottom, t=top):
                return b + 30 * (u - 0.5), t + 30 * (v - 0.5)

            def sampler(u, v):
                lo, hi = layer(np.asarray(u, np.float64), np.asarray(v, np.float64))
                return np.stack(np.broadcast_arrays(lo, hi, 0.0, 0.0), -1)
            mod = np.zeros((4, 4))
            mod[0, 0] = np.array([(FRAME & 0xFFFF) << 16 | 28], np.uint32).view(np.float32)[0]
            cloud = np.zeros((4, 4))
            cloud[3, 3] = 34.5
            cbs = {"b0 space50": mod, "b1 space50": cloud, 720: scene(rng, [(0.45, 0.43)] * 4)[2], 4800: glob}
            ev = Evaluator(text, cbs, {}, sampler=sampler)
            got = float(np.asarray(ev("%mod.vc.v")).ravel()[0])
            want = camera_sun_ref(glob, layer)
            if abs(got - want) > 2e-4:
                failures.append(f"{blob.name} {label}: sunlight at the camera {got:.5f}, want {want:.5f}")
            stores_ = stores(text)
            v, hframe = stores_[P.SIL_REGION + 80][:2]
            if v != "%mod.vc.v":
                failures.append(f"{blob.name}: header +80 stores {v}, not the camera's sunlight")
            hf = int(np.asarray(ev(hframe)).astype(np.float32).view(np.int32).ravel()[0])
            if hf != ((FRAME & 0xFFFF) | 0x10000):
                failures.append(f"{blob.name}: header frame {hf:#x}")
            print(f"{blob.name} sunlight at the camera, {label}: {got:.4f} (reference {want:.4f})")


SPHERES = [((x, 1.0, 0.0), 2.2) for x in np.linspace(-30, 3, 12)] + \
          [((-12.0, 0.0, z), 1.6) for z in np.linspace(-16, 16, 11)]  # a crude aircraft, camera-relative world (m)


def hits(origin, direction, tmax):
    """Mock TLAS: does each ray (origin + t dir, 0 <= t <= tmax) hit a sphere?"""
    o = np.stack(origin, -1).astype(np.float64)
    d = np.stack(direction, -1).astype(np.float64)
    d /= np.linalg.norm(d, axis=-1, keepdims=True)
    out = np.zeros(o.shape[:-1], bool)
    for c, r in SPHERES:
        oc = o - np.array(c)
        b = (oc * d).sum(-1)
        disc = b * b - ((oc * oc).sum(-1) - r * r)
        sq = np.sqrt(np.maximum(disc, 0))
        t0, t1 = -b - sq, -b + sq
        out |= (disc >= 0) & (t1 >= 0) & (t0 <= tmax)
    return out


def check_rays(rng, failures):
    """With the TLAS bound (DebugCapture bit 5) and an empty shadow map (RT
    shadows), the window comes from the traced rays: every traced ray's origin,
    direction and length is evaluated from the IR and run against the mock
    spheres; the stored pyramid must match an independent numpy trace of the
    cells (the light plane's axes from the cascade rows through R, the band
    +-(A + 20) m along the light)."""
    text, ev, R, sun_w, rows, xs, ys = run(rng, 34.5, [(0.45, 0.43)] * 4, rt_on=True, rt_bound=True)
    # each StateScalar answers the last TraceRayInline before it
    trace = re.compile(r"call void @dx\.op\.rayQuery_TraceRayInline\(i32 179, i32 \S+, %dx\.types\.Handle \S+, "
                       r"i32 \S+, i32 \S+, " + ", ".join([r"float (\S+)"] * 8) + r"\)")
    last = None
    for line in text.splitlines():
        if m := trace.search(line):
            last = [m[k] for k in range(1, 9)]
        elif m := re.match(r"\s+(%\S+) = call i32 @dx\.op\.rayQuery_StateScalar\.i32", line):
            vals = [np.asarray(ev(v) if v.startswith("%") else ev.val(v), np.float64) for v in last]
            ox, oy, oz, _, dx, dy, dz, tmax = np.broadcast_arrays(*vals)
            ev.env[m[1]] = hits((ox, oy, oz), (dx, dy, dz), tmax).astype(np.int64)
    for tag in ("l0", "ln"):
        ev.env[f"%mod.sl.{tag}rc"] = np.asarray(ev(f"%mod.sl.{tag}rv"))
    go = np.asarray(ev("%mod.sl.go"), bool)
    is0, isn = np.asarray(ev("%mod.sl.is0"), bool) & go, np.asarray(ev("%mod.sl.isn"), bool) & go
    mem = {}
    for addr_n, val_n, sel in (("%mod.sl.a.o", "%mod.sl.av", is0), ("%mod.sl.b.o", "%mod.sl.bv", isn)):
        for addr, val in zip(np.asarray(ev(addr_n))[sel], np.asarray(ev(val_n))[sel]):
            mem[int(addr)] = float(val)
    # reference: the same cells traced in numpy
    k = 2
    w = P.SIL_SPAN_FACTOR * 34.5
    a = w / 2
    c0 = w / P.SIL_N0
    world = [rows[5 * k + r, :3] @ R for r in range(3)]
    e_u, e_v, lsun = (x / np.linalg.norm(x) for x in world)
    band = a + P.SIL_BAND_MARGIN_M
    worst, covered = 0.0, 0
    for L in range(P.SIL_LEVELS):
        n = P.SIL_N0 >> L
        taps = P.SIL_RAYS0 if L == 0 else P.SIL_RAYS
        j, i = np.mgrid[0:n, 0:n]
        cov = np.zeros((n, n))
        for p in range(taps):
            for q in range(taps):
                xa = ((i + (p + 0.5) / taps) * 2 ** L - P.SIL_N0 / 2) * c0
                yb = ((j + (q + 0.5) / taps) * 2 ** L - P.SIL_N0 / 2) * c0
                o = [xa * e_u[c] + yb * e_v[c] + band * lsun[c] for c in range(3)]
                cov += hits(o, [np.full_like(xa, -lsun[c]) for c in range(3)], np.full_like(xa, 2 * band))
        cov /= taps * taps
        cov[0, :] = cov[-1, :] = cov[:, 0] = cov[:, -1] = 0
        base = P.SIL_REGION + P.SIL_OFFSETS[L]
        got = np.array([mem.get(base + 4 * idx, np.nan) for idx in range(n * n)]).reshape(n, n)
        if np.isnan(got).any():
            failures.append(f"rays: level {L}: cells not written")
            continue
        worst = max(worst, float(np.abs(got - cov).max()))
        if L == 0:
            covered = int((cov > 0).sum())
    if worst > 1e-6 or covered < 200:
        failures.append(f"rays: coverage off by up to {worst:.3g} ({covered} level-0 cells covered)")
    flag_any = np.asarray(ev("%mod.sl.a.any"), bool)[is0].any()
    if not flag_any:
        failures.append("rays: the flag isn't written")
    print(f"TLAS rays (RT shadows, empty map): {covered} level-0 cells covered, coverage max error {worst:.1e}")


def main():
    rng = np.random.default_rng(7)
    failures = []
    centred = [(0.45, 0.43)] * 4
    check_case("cockpit A320", rng, 34.5, centred, 2, failures)
    check_case("external view", rng, -34.5, centred, 2, failures)
    check_case("no aircraft data", rng, 0.0, centred, 2, failures)
    # a 24 m window (the minimum) can't fit the 20 m cascade: cascade 2
    check_case("small aircraft", rng, 8.0, [(0.45, 0.43), (0.5, 0.5), (0.45, 0.43), (0.45, 0.43)], 2, failures)
    check_case("near cascade 2's edge", rng, 34.5, [(0.45, 0.43), (0.45, 0.43), (0.1, 0.43), (0.45, 0.43)], 3,
               failures)
    check_case("RT shadows on (empty map)", rng, 34.5, centred, 2, failures, rt_on=True)
    check_camera_sun(rng, failures)
    check_rays(rng, failures)
    if failures:
        print("\nFAILED:\n  " + "\n  ".join(failures))
        raise SystemExit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()

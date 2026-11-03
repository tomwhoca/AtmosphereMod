"""Check the cloud haze-colour patch (patch_dxil.emit_cloud_haze_colour).

    python tools/test_cloud_ap.py [--alt M] [--pitch DEG]

1. Runs `atmolab dump --model` for real constants (capture + lab camera +
   build/tunables.ini) and our GPU camera volume.
2. Evaluates the IR the patcher emits (prologue + haze colour) with a small
   interpreter, and compares it with an independent numpy version of the same
   maths (written from our medium model): this checks the generator.
3. Compares the march's mean transmittance with our camera volume's 1 - alpha
   at the same rays and depths: this checks ray direction, camera position and
   units against the GPU replica.
"""

import argparse
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import patch_dxil as P  # noqa: E402

F = np.float32


# ---------------------------------------------------------------- IR interpreter

def literal(tok: str):
    if tok.startswith("0x"):
        return F(struct.unpack("<d", struct.pack("<Q", int(tok, 16)))[0])
    return F(float(tok))


def run_ir(lines, env, cbuffers):
    """Evaluate straight-line IR of the forms the patcher emits."""
    def val(tok):
        return env[tok] if tok.startswith("%") else literal(tok)

    for line in lines:
        name, rhs = line.strip().split(" = ", 1)
        if m := re.match(r"call %dx\.types\.Handle @dx\.op\.createHandleFromBinding\(i32 217, "
                         r"%dx\.types\.ResBind \{ i32 (\d+), i32 \d+, i32 (\d+), i8 2 \}", rhs):
            env[name] = f"b{m[1]} space{m[2]}"
        elif m := re.match(r"call %dx\.types\.Handle @dx\.op\.annotateHandle\(i32 216, %dx\.types\.Handle (\S+),", rhs):
            env[name] = env[m[1]]
        elif m := re.match(r"call %dx\.types\.CBufRet\.f32 @dx\.op\.cbufferLoadLegacy\.f32\(i32 59, "
                           r"%dx\.types\.Handle (\S+), i32 (\d+)\)", rhs):
            env[name] = cbuffers[env[m[1]]][int(m[2])]
        elif m := re.match(r"extractvalue %dx\.types\.CBufRet\.f32 (\S+), (\d)", rhs):
            env[name] = env[m[1]][..., int(m[2])]
        elif m := re.match(r"(fmul|fadd|fsub|fdiv) fast float (\S+), (\S+)", rhs):
            a, b = val(m[2]), val(m[3])
            env[name] = {"fmul": a * b, "fadd": a + b, "fsub": a - b, "fdiv": a / b}[m[1]].astype(F)
        elif m := re.match(r"call float @dx\.op\.unary\.f32\(i32 (\d+), float (\S+)\)", rhs):
            a = val(m[2])
            env[name] = {"6": np.abs(a), "7": np.clip(a, 0, 1), "21": np.exp2(a), "23": np.log2(a), "24": np.sqrt(a)}[m[1]].astype(F)
        elif m := re.match(r"call float @dx\.op\.binary\.f32\(i32 (35|36), float (\S+), float (\S+)\)", rhs):
            a, b = val(m[2]), val(m[3])
            env[name] = (np.maximum(a, b) if m[1] == "35" else np.minimum(a, b)).astype(F)
        elif m := re.match(r"call float @dx\.op\.tertiary\.f32\(i32 46, float (\S+), float (\S+), float (\S+)\)", rhs):
            env[name] = (val(m[1]) * val(m[2]) + val(m[3])).astype(F)
        elif m := re.match(r"fcmp fast (olt|ole|ogt) float (\S+), (\S+)", rhs):
            a, b = val(m[2]), val(m[3])
            env[name] = {"olt": a < b, "ole": a <= b, "ogt": a > b}[m[1]]
        elif m := re.match(r"select i1 (\S+), float (\S+), float (\S+)", rhs):
            env[name] = np.where(val(m[1]), val(m[2]), val(m[3])).astype(F)
        else:
            raise ValueError(f"interpreter: unsupported line: {line}")
    return env


# ---------------------------------------------------------------- reference (numpy, from the HLSL)

def reference(u, v, depth_m, tgrey, g, a, t, samples=P.CLOUD_AP_SAMPLES, newton=P.CLOUD_AP_NEWTON_STEPS):
    mix = np.clip(t[0], 0, 1)
    clip = np.stack([u * 2 - 1, 1 - v * 2, np.zeros_like(u), np.ones_like(u)], -1)
    h = clip @ np.stack([g[174], g[175], g[176], g[177]]).T
    vr = h[..., :3] / h[..., 3:4]
    d = vr @ np.stack([g[134][:3], g[135][:3], g[136][:3]])
    d /= np.linalg.norm(d, axis=-1, keepdims=True)
    R = a[2][3]
    cam = np.array([g[132][0] * 1e-3, R + max(0.0, (g[132][1] + g[117][1]) * 1e-3), g[132][2] * 1e-3])
    ref = mix * np.clip(t[8], 0, 1)
    ray = a[1][:3] + (np.array(P.REFERENCE_RAYLEIGH) - a[1][:3]) * ref
    ozone_scale = t[13] if t[13] > 0 else 1.0  # (ModParams header word 3)
    ozo = a[0][:3] + (np.array(P.REFERENCE_OZONE) * ozone_scale - a[0][:3]) * ref
    spec = (np.exp2(np.clip(t[10], 0, 3) * np.array(P.AEROSOL_SPECTRAL_LOG2)) - 1) * mix + 1
    mie = a[3][:3] * spec
    brk = mix * t[6] * a[2][1] * np.array(P.BROWN_HAZE_TINT)
    edge, fts = max(t[2], 0.001), max(t[5], 0.001)
    dt = depth_m * 1e-3 / samples
    tau = np.zeros(u.shape + (3,))
    def height_at(tk):
        return np.linalg.norm(cam + d * tk[..., None], axis=-1) - R

    def integral(hk):  # the boundary layer's integral, as in the model
        x = (hk - t[1]) / edge
        return t[3] * (hk - edge * (np.maximum(x, 0) + np.log1p(np.exp(-np.abs(x)))))

    for i in range(samples):
        p = cam + d * (dt * (i + 0.5))[..., None]
        hh = np.linalg.norm(p, axis=-1) - R
        h0, h1 = height_at(dt * i), height_at(dt * (i + 1))
        dh = h1 - h0
        bl_mid = t[3] / (1 + np.exp((hh - t[1]) / edge))
        with np.errstate(divide="ignore", invalid="ignore"):
            bl = np.where(np.abs(dh) > 0.01, (integral(h1) - integral(h0)) / np.where(np.abs(dh) > 0.01, dh, 1), bl_mid)
        ft = t[4] * np.exp(-hh / fts)
        dms = np.exp(a[4][3] * hh)
        dm = dms if mix <= 0 else dms + (bl + ft - dms) * mix
        dr = np.exp(a[5][3] * hh)
        o = np.clip(np.where(hh < a[6][0], a[6][2] * hh + a[6][1], a[7][1] * hh + a[7][0]), 0, 1)
        tau += (dm[..., None] * mie + dr[..., None] * ray + o[..., None] * ozo + bl[..., None] * brk) * dt[..., None]
    trans = np.exp(-tau)  # the march's own transmittance (for the geometry check)
    # Spectral shape from the march, amount from the volume's grey transmittance.
    tau_mean = tau.mean(-1, keepdims=True)
    shape = np.where(tau_mean < 1e-6, 1.0, tau / np.maximum(tau_mean, 1e-6))
    target = np.maximum(tgrey, 1e-4)[..., None]
    te = -np.log(target)
    for _ in range(newton):
        e = np.exp(-shape * te)
        f = e.mean(-1, keepdims=True) - target
        slope = (shape * e).mean(-1, keepdims=True)
        te = np.maximum(te + f / np.maximum(slope, 1e-6), 0.0)
    tinted = np.exp(-shape * te)
    w = mix * np.clip(t[11], 0, 1)
    return tgrey[..., None] + (tinted - tgrey[..., None]) * w, trans


def tint_tail(tau, tgrey, weight):
    """numpy: patch_dxil.emit_haze_tint (the spectral shape of tau, the mean of tgrey)."""
    mean = tau.mean(-1, keepdims=True)
    shape = np.where(mean < 1e-6, 1.0, tau / np.maximum(mean, 1e-6))
    target = np.maximum(tgrey, 1e-4)[..., None]
    te = -np.log(target)
    for _ in range(P.CLOUD_AP_NEWTON_STEPS):
        e = np.exp(-shape * te)
        slope = (shape * e).mean(-1, keepdims=True)
        te = np.maximum(te + (e.mean(-1, keepdims=True) - target) / np.maximum(slope, 1e-6), 0.0)
    out = tgrey[..., None] + (np.exp(-shape * te) - tgrey[..., None]) * weight[..., None]
    return np.where(weight[..., None] > 0, out, tgrey[..., None])


def check_tint_from_volume() -> list[str]:
    """The haze colour from the camera volume (CLOUD_AP_FROM_VOLUME): the volume's
    store (patch_medium tint) and the view march's lookup (emit_cloud_haze_colour_volume)."""
    import csv
    from test_medium_patch import Evaluator
    failures = []
    rng = np.random.default_rng(9)
    nx, ny, nz = P.CAMVOL_N
    frame = 4321

    def flags(bits, at=frame):
        mod = np.zeros((4, 4))
        mod[0, 0] = np.uint32(bits | (at << 16)).view(np.float32)
        return mod

    # the volume: each cell's -ln T per colour, the frame, at (z * ny + y) * nx + x
    dxc = P.find_dxc()
    index = {Path(r["path"]).name.split(".")[0]: r for r in csv.DictReader(open(ROOT / "extracted" / "index.csv"))}
    stock = P.disassemble(ROOT / "extracted" / index["0x7d2ec0ce"]["path"], dxc)
    patched, summary = P.patch_medium(stock, far_slice=True, dump="camera", field=True, ms_rows="read", tint=True)
    trans = re.search(r"\(T (%\S+), (%\S+), (%\S+)\)", summary).groups()
    store = next(l for l in patched.splitlines() if "rawBufferStore" in l and "%mod.vt.b" in l)
    addr, *vals = re.match(r"\s+call void @dx\.op\.rawBufferStore\.f32\(i32 140, %dx\.types\.Handle \S+, i32 (\S+), "
                           r"i32 undef, float (\S+), float (\S+), float (\S+), float (\S+), i8 15", store).groups()
    go = re.search(r"br i1 (\S+), label %mod\.vt\.go,", patched)[1]
    coords = re.search(r"textureStore\.f32\(i32 67, %dx\.types\.Handle \S+, i32 (\S+), i32 (\S+), i32 (\S+), "
                       r"float \S+, float \S+, float \S+, float %\d+, i8 15\)", stock).groups()
    n = 4096
    t_seed = np.concatenate([[0.0, 1e-38, 1.0, 1.0 + 1e-6], rng.uniform(1e-6, 1.0, n * 3 - 4)]).reshape(n, 3)
    xyz = [rng.integers(0, size + 3, n) for size in P.CAMVOL_N]  # (some outside the volume)
    seeds = {name: t_seed[:, c].astype(F) for c, name in enumerate(trans)}
    seeds.update({name: xyz[k].astype(np.int64) for k, name in enumerate(coords)})
    for bits in (P.DEBUG_BUFFER_TYPES, 0):
        ev = Evaluator(patched, {"b0 space50": flags(bits)}, seeds)
        inside = (xyz[0] < nx) & (xyz[1] < ny) & (xyz[2] < nz)
        if not np.array_equal(np.asarray(ev(go), bool), inside & (bits != 0)):
            failures.append(f"volume store: written {'outside the volume' if bits else 'without the 20 MB buffer'}")
        if not bits:
            continue
        want_addr = P.CAMVOL_TINT_REGION + 16 * ((xyz[2] * ny + xyz[1]) * nx + xyz[0])
        if not np.array_equal(np.asarray(ev(addr), np.int64)[inside], want_addr[inside]):
            failures.append("volume store: wrong cell")
        want = np.maximum(-np.log(np.maximum(t_seed.astype(F).astype(np.float64), 1e-30)), 0.0)
        got = np.stack([np.asarray(ev(v), np.float64) for v in vals[:3]], -1)
        err = float(np.max(np.abs(got - want) / np.maximum(want, 1e-3)))
        if err > 1e-5:
            failures.append(f"volume store: -ln T rel. error {err:.1e}")
        stamp = np.asarray(ev(vals[3]), np.float32).view(np.uint32)
        if not np.all(stamp == (frame | 0x10000)):
            failures.append("volume store: wrong stamp")

    # the view march: the 8 cells around the lookup, trilinear, then the tint
    cells = rng.uniform(0.0, 3.0, (nz, ny, nx, 3))  # -ln T per colour
    words = np.zeros((P.CAMVOL_TINT_REGION + 16 * nx * ny * nz) // 4, F)
    region = words[P.CAMVOL_TINT_REGION // 4:].reshape(nz, ny, nx, 4)
    region[..., :3] = cells
    em = P.Emitter("ca")
    outs = P.emit_cloud_haze_colour_volume(em, "%u", "%v", "%w", "%tg", "%wt")
    text = "\n".join(P.prologue_lines() + em.lines)
    n = 6000
    u, v, w = (rng.uniform(-0.05, 1.05, n) for _ in range(3))  # (edges: clamped to the outer cells)
    tgrey = rng.uniform(0.05, 1.0, n)
    weight = np.where(rng.uniform(0, 1, n) < 0.1, 0.0, rng.uniform(0.0, 1.0, n))
    seeds = {"%u": u.astype(F), "%v": v.astype(F), "%w": w.astype(F), "%tg": tgrey.astype(F), "%wt": weight.astype(F)}
    f = [c.astype(F).astype(np.float64) * size - 0.5 for c, size in ((u, nx), (v, ny), (w, nz))]
    base = [np.clip(np.floor(fk), 0, size - 2).astype(int) for fk, size in zip(f, P.CAMVOL_N)]
    fr = [np.clip(fk - b, 0, 1) for fk, b in zip(f, base)]
    tau = 0
    for dz in (0, 1):
        for dy in (0, 1):
            for dx in (0, 1):
                wgt = (fr[0] if dx else 1 - fr[0]) * (fr[1] if dy else 1 - fr[1]) * (fr[2] if dz else 1 - fr[2])
                tau = tau + wgt[:, None] * cells[base[2] + dz, base[1] + dy, base[0] + dx]
    for name, written_at, ok in (("fresh", frame - P.CAMVOL_TINT_FRESH_FRAMES, True),
                                 ("stale", frame - P.CAMVOL_TINT_FRESH_FRAMES - 1, False), ("unwritten", None, False)):
        region[..., 3] = (np.full((nz, ny, nx), 0, np.uint32) if written_at is None else
                          np.full((nz, ny, nx), (written_at & 0xFFFF) | 0x10000, np.uint32)).view(F)
        ev = Evaluator(text, {"b0 space50": flags(P.DEBUG_BUFFER_TYPES)}, seeds, buffer=words)
        got = np.stack([np.asarray(ev(o), np.float64) for o in outs], -1)
        want = tint_tail(tau, tgrey, weight if ok else np.zeros(n))
        err = float(np.max(np.abs(got - want)))
        if err > 2e-4:
            failures.append(f"view march, {name} cells: abs. error {err:.1e}")
    return failures


def read_pfm(path):
    data = path.read_bytes()
    header = data.split(b"\n", 3)
    w, h = map(int, header[1].split())
    channels = 3 if header[0] == b"PF" else 1
    img = np.frombuffer(header[3], dtype="<f4").reshape(h, w, channels)
    return np.flipud(img)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--alt", default="3500")
    ap.add_argument("--pitch", default="-5")
    ap.add_argument("--apdist", default="356700")
    args = ap.parse_args()

    out = Path(tempfile.mkdtemp(prefix="cloud_ap_"))
    subprocess.run([str(ROOT / "build" / "lab" / "atmolab.exe"), "dump", str(out), "--model", "--alt", args.alt,
                    "--pitch", args.pitch, "--apdist", args.apdist, "--sun", "20"],
                   check=True, capture_output=True, cwd=ROOT / "build" / "lab")
    g = np.fromfile(out / "cb_global.bin", "<f4").reshape(-1, 4)
    a = np.fromfile(out / "cb_atmo.bin", "<f4").reshape(-1, 4)
    mod = np.fromfile(out / "mod_params.bin", "<f4").reshape(-1, 4)
    t_lab = np.concatenate([mod[1:4].ravel(), mod[0, 2:4]])  # T0..T11, T12, T13: as atmolab ran
    # The IR check also covers the fitted sky colours (T8) and the ozone scale
    # (header word 3, where atmolab writes T13).
    mod[3, 0], mod[0, 3] = 1.0, 1.3
    t = np.concatenate([mod[1:4].ravel(), mod[0, 2:4]])  # T0..T11, T12, ozone scale

    # Rays through froxel centres (with the volume's jitter) and depths at slice centres.
    jit = a[12][:2]
    xs, ys, zs = np.meshgrid(np.arange(32), np.arange(32), np.arange(64), indexing="ij")
    u = ((xs + 0.5) / 32 + jit[0]).astype(F)
    v = ((ys + 0.5) / 32 + jit[1]).astype(F)
    depth = (((zs + 0.5) / 64) ** 2 * g[10][1]).astype(F)
    rng = np.random.default_rng(1)
    tgrey = rng.uniform(0.05, 1.0, u.shape).astype(F)

    em = P.Emitter("ca")
    outs = P.emit_cloud_haze_colour(em, "%u", "%v", "%d", "%tg", "%gb", "%ab")
    env = {"%u": u, "%v": v, "%d": depth, "%tg": tgrey, "%gb": "global", "%ab": "atmo"}
    cbuffers = {"global": g.astype(F), "atmo": a.astype(F), "b0 space50": mod.astype(F)}
    run_ir(P.prologue_lines() + em.lines, env, cbuffers)
    ir_out = np.stack([env[o] for o in outs], -1)
    ref_out, _ = reference(u.astype(np.float64), v.astype(np.float64), depth.astype(np.float64),
                           tgrey.astype(np.float64), g.astype(np.float64), a.astype(np.float64),
                           t.astype(np.float64))
    _, trans = reference(u.astype(np.float64), v.astype(np.float64), depth.astype(np.float64),
                         tgrey.astype(np.float64), g.astype(np.float64), a.astype(np.float64),
                         t_lab.astype(np.float64))  # (the geometry check: atmolab's own volume)
    err = np.abs(ir_out - ref_out)
    worst = np.unravel_index(err.argmax(), err.shape)
    print(f"tunables T0..T13: {np.round(t, 3).tolist()}")
    converged, _ = reference(u.astype(np.float64), v.astype(np.float64), depth.astype(np.float64),
                             tgrey.astype(np.float64), g.astype(np.float64), a.astype(np.float64),
                             t.astype(np.float64), newton=30)
    print(f"brightness kept: |mean(tinted) - grey| max {np.abs(ref_out.mean(-1) - tgrey).max():.1e} "
          f"({P.CLOUD_AP_NEWTON_STEPS} Newton steps; vs converged: max {np.abs(ref_out - converged).max():.1e})")
    print(f"generated IR vs numpy reference over {u.size} rays x depths: abs err median {np.median(err):.1e}, "
          f"99.9% {np.percentile(err, 99.9):.1e}, max {err.max():.1e} (at reference {ref_out[worst]:.3f}, "
          f"depth {depth[worst[:3]] / 1000:.0f} km)")

    # Geometry: mean transmittance of the march vs our GPU camera volume.
    vol_alpha = read_pfm(out / "ours_ch_camera_volume_alpha.pfm")[..., 0].reshape(64, 32, 32)  # z, y, x
    vol_t = 1.0 - vol_alpha.transpose(2, 1, 0)  # x, y, z
    march_t = trans.mean(-1)
    # The volume pulls voxels below the ground onto it; clouds are never there, so
    # compare only depths short of the ray's ground hit.
    clip = np.stack([u * 2 - 1, 1 - v * 2, np.zeros_like(u), np.ones_like(u)], -1).astype(np.float64)
    hv = clip @ np.stack([g[174], g[175], g[176], g[177]]).T
    d = (hv[..., :3] / hv[..., 3:4]) @ np.stack([g[134][:3], g[135][:3], g[136][:3]])
    d /= np.linalg.norm(d, axis=-1, keepdims=True)
    R = float(a[2][3])
    cam_r = R + float(g[117][1]) * 1e-3
    b = cam_r * d[..., 1]
    disc = b * b - (cam_r ** 2 - R ** 2)
    hit = np.where((disc > 0) & (b < 0), -b - np.sqrt(np.maximum(disc, 0)), np.inf)
    ground = depth * 1e-3 < hit
    for z0, z1 in ((4, 16), (16, 32), (32, 48), (48, 63)):
        sel = (zs >= z0) & (zs < z1) & ground
        diff = np.abs(march_t[sel] - vol_t[sel])
        print(f"  slices {z0:2}-{z1 - 1:2} ({depth[0, 0, z0] / 1000:6.1f}-{depth[0, 0, z1 - 1] / 1000:6.1f} km): "
              f"mean T march {march_t[sel].mean():.3f} volume {vol_t[sel].mean():.3f}, "
              f"|diff| median {np.median(diff):.4f}, 95% {np.percentile(diff, 95):.4f}")
    far = trans[:, :, 40:].reshape(-1, 3)
    print(f"  example far-slice transmittance (R, G, B): {np.round(far[len(far) // 2], 3).tolist()}")
    # float32 (IR) against float64 (reference): rare worst cases reach ~1e-3.
    ok = np.percentile(err, 99.9) < 1e-3 and err.max() < 5e-3
    tint_failures = check_tint_from_volume()
    print("haze colour from the camera volume:", "; ".join(tint_failures) if tint_failures else "as numpy")
    ok = ok and not tint_failures
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

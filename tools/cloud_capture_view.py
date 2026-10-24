"""Render a weather volume capture (patch_cloud_gen_capture, DebugCapture bit 6).

    python tools/cloud_capture_view.py 40        # build/logs/captures/40_clouds.bin

Prints the generator's constants and a per-slice profile, and writes
<id>_clouds.png beside the capture:
- what MSFS feeds the generator: in live weather the weather map (t5/t6
  lerped: one value C per texel and slice), as the highest and lowest cloudy
  slice per texel and a profile of how much of the map is cloudy at each
  altitude; with presets or injected weather (Active Sky) the three layers
  (base, top, coverage, density, noise mix) and the 2D map where bound;
- what the generator makes of it (the output volume): cloud tops, bases and
  a vertical section through the camera, over the whole disc (every 4th
  column) and at full resolution in the 128 x 128 columns around the centre;
- the four shape-noise channels (t4), the coverage curve (t7) and the
  precipitation map.
Layout: patch_dxil.py, CLOUD_GEN_REGION.
"""

import argparse
import struct
import sys
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import patch_dxil as P  # noqa: E402

CAPTURES = ROOT / "build" / "logs" / "captures"
K = P.CG_SLICES
EDGES = 15.625 * np.arange(K + 1) ** 2          # slice boundaries (m above sea level)
CENTRES = 15.625 * (np.arange(K) + 0.5) ** 2


def load(capture: str) -> dict:
    raw = (CAPTURES / f"{capture}_clouds.bin").read_bytes()
    if len(raw) < P.CG_PRECIP:
        raise SystemExit(f"{capture}_clouds.bin: {len(raw)} bytes, too short")
    raw = raw + bytes(max(0, P.CG_END - len(raw)))  # (older captures end before the newer regions)
    stamp, version, flags, n = struct.unpack_from("<4I", raw, 0)
    # (flags 0, from the first build: a layer-family variant, its textures not dumped)
    d = {"stamp": stamp, "version": version, "N": n, "textures": bool(flags & 1),
         "layers": bool(flags & 2) or flags == 0, "has_noise": bool(flags & 3), "map2d": bool(flags & P.CG_MAP2D_FLAG)}
    d["dims"] = {reg: struct.unpack_from("<4I", raw, P.CG_DIMS + 16 * j) for j, reg in enumerate(P.CG_DIM_TEXTURES)}
    d["U"] = np.frombuffer(raw, np.float32, 4 * P.CG_USER_REGS, P.CG_USER).reshape(-1, 4)
    d["Ui"] = np.frombuffer(raw, np.int32, 4 * P.CG_USER_REGS, P.CG_USER).reshape(-1, 4)
    d["P"] = np.frombuffer(raw, np.float32, 4 * P.CG_POST_REGS, P.CG_POST).reshape(-1, 4)
    d["lut"] = np.frombuffer(raw, np.float32, P.CG_LUT_N, P.CG_LUT)
    m = P.CG_MAP_N
    d["maps"] = [np.frombuffer(raw, np.uint8, m * m * K, off).reshape(m, m, K) / 255.0 for off in P.CG_MAP]
    q = P.CG_NOISE_N
    d["noise"] = np.frombuffer(raw, np.uint8, q * q * K * 4, P.CG_NOISE).reshape(q, q, K, 4) / 255.0
    v = P.CG_VOL_N
    d["vol"] = np.frombuffer(raw, np.uint8, v * v * K * 4, P.CG_VOL).reshape(v, v, K, 4) / 255.0
    d["volsub"] = np.frombuffer(raw, np.uint8, v * v * K * 4, P.CG_VOLSUB).reshape(v, v, K, 4) / 255.0
    d["precip"] = [np.frombuffer(raw, np.float32, m * m, P.CG_PRECIP + 4 * m * m * j).reshape(m, m) for j in range(2)]
    d["g4"] = np.frombuffer(raw, np.float32, m * m * 4, P.CG_MAP[0]).reshape(m, m, 4)
    d["cloud"] = np.frombuffer(raw, np.float32, P.CLOUD_PARAMS_SIZE // 4, P.CG_CLOUD)
    d["cloud_u"] = np.frombuffer(raw, np.uint32, P.CLOUD_PARAMS_SIZE // 4, P.CG_CLOUD)
    d["columns"] = np.frombuffer(raw, np.float32, v * v * 4, P.CG_COLUMNS).reshape(v, v, 4)
    # the METAR anchoring's station table (the add-on writes it; patch_dxil.py ANCHOR_TABLE)
    magic, count, ns = struct.unpack_from("<3I", raw, P.CG_ANCHOR)
    ns, nr = ns or 6, P.ANCHOR_ROWS  # (the first tables had 6 stations and no count)
    d["anchor"] = magic == P.ANCHOR_MAGIC
    d["anchor_stations"] = np.frombuffer(raw, np.float32, 4 * ns, P.CG_ANCHOR + 16).reshape(ns, 4)
    d["anchor_rows"] = np.frombuffer(raw, np.float32, 4 * ns * nr, P.CG_ANCHOR + 16 + 16 * ns).reshape(ns, nr, 4)
    return d


def coverage(vol):
    """Coverage and extinction profile B per texel, as the cloud passes read the volume."""
    x, y = vol[..., 0], vol[..., 1]
    return np.clip(50 * x - 24.5, 0, 1), (2 * y - 1) ** 2


def top_base(mask):
    """Highest and lowest slice (altitude in km, NaN if none) per column of a [.., K] mask."""
    any_ = mask.any(-1)
    top = np.where(any_, CENTRES[K - 1 - np.argmax(mask[..., ::-1], -1)], np.nan) / 1000
    base = np.where(any_, CENTRES[np.argmax(mask, -1)], np.nan) / 1000
    return top, base


def section(ax, field, extent_km, title, vmax=None, cmap="Blues", xlabel="km"):
    """A vertical section: field [x, K] over x (km) with metric slice heights."""
    xs = np.linspace(extent_km[0], extent_km[1], field.shape[0] + 1)
    im = ax.pcolormesh(xs, EDGES / 1000, field.T, cmap=cmap, vmin=0, vmax=vmax, shading="flat")
    ax.set_ylim(0, 16)
    ax.set_ylabel("altitude (km MSL)")
    ax.set_xlabel(xlabel)
    ax.set_title(title, fontsize=9)
    return im


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("capture")
    args = ap.parse_args()
    d = load(args.capture)
    U, Pc, Ui = d["U"], d["P"], d["Ui"]
    n = d["N"]
    s = Pc[4, 2]
    print(f"capture {args.capture}: layout {d['version']}, {n} x {n} columns, volume scale s = {s:.0f} m "
          f"(centre texel {s / max(n, 1):.0f} m), centre ({Pc[4, 0]:.0f}, {Pc[4, 1]:.0f}) m from the camera")
    if U[2, 2]:
        print(f"shape noise t4: period {1 / U[2, 2]:.0f} x {1 / U[2, 3]:.0f} m, offset ({U[2, 0]:.0f}, {U[2, 1]:.0f})")
    if Pc[0, 0] or Pc[2, 2]:
        sx = np.hypot(Pc[0, 0], Pc[0, 2])
        print(f"weather map: {1 / sx / 1000 if sx else float('nan'):.0f} km across (uv rows P[0] {Pc[0]}, "
              f"P[2] {Pc[2]}); keyframe lerp clouds {Pc[6, 0]:.3f}, precipitation {Pc[6, 1]:.3f}")
    if d["layers"]:
        print("layer generator (presets, injected weather): base, top in m "
              + ("above the terrain" if U[6, 3] > 0 else "MSL") + f"; precipitation {U[8, 3]:g}")
        for j, name in enumerate("xyz"):
            print(f"  layer {j}: {U[6, j]:7.0f} - {U[7, j]:7.0f} m, coverage {U[8, j]:.3f}, density {U[9, j]:.3f}, "
                  f"noise mix (x y z w) {np.round(U[3 + j], 3)}")
    cc, cu = d["cloud"], d["cloud_u"]
    if d["anchor"]:
        print(f"METAR anchoring as the generator received it: strength {cc[20]:.2f}, radius {cc[21]:.0f} km")
        for i, (x, y, z, w) in enumerate(d["anchor_stations"]):
            if x == y == z == 0:
                continue
            rows = d["anchor_rows"][i]
            described = [k for k in range(P.WEATHER_SLICES) if rows[k, 1] > 0]
            top = 15.625 * (max(described) + 1) ** 2 if described else 0.0
            cloud = ", ".join(f"{k}:{rows[k, 0]:.2f}" + ("d" if rows[k, 2] > 0.5 else "") + ("s" if rows[k, 3] > 0.5 else "")
                              for k in described if rows[k, 0] > 0)
            print(f"  station {i}: {np.degrees(np.arcsin(np.clip(z, -1, 1))):.3f} "
                  f"{np.degrees(np.arctan2(y, x)):.3f}, weight {w:.2f}; describes up to {top:.0f} m; slice:C "
                  f"(d deck, s smooth) {cloud or 'clear'}")
    elif cc[23] or cc[24]:  # (captures before the station table: one station in C22..C27)
        print(f"METAR anchoring as the generator received it: strength {cc[20]:.2f}, radius {cc[21]:.0f} km, "
              f"station {np.degrees(cc[22]):.3f} {np.degrees(cc[23]):.3f}, MSFS's own above {cc[24]:.0f} m")
        for j in range(3):
            w = int(cu[25 + j])
            if w:
                print(f"  layer {j}: {(w & 8191) * 2}-{((w >> 13) & 8191) * 2} m, C {((w >> 26) & 15) / 15:.2f}, "
                      f"kind {w >> 30}")
    if d["anchor"] or cc[23] or cc[24]:
        col = d["columns"]
        mid = P.CG_VOL_N // 2
        print(f"  centre column: anchor weight {col[mid, mid, 2]:.2f}, {col[mid, mid, 3] / 1000:.1f} km from the "
              f"nearest station; local terrain {col[mid, mid, 0]:.0f} m, global terrain {col[mid, mid, 1]:.0f} m; "
              f"window terrain {col[..., 0].min():.0f}..{col[..., 0].max():.0f} m")
    stations = int(Ui[6, 0]) if d["textures"] else 0
    if 0 < stations < 40:
        print(f"METAR stations: {stations}")
        for i in range(stations):
            x, z, p, f = U[10 + i]
            print(f"  {i}: at ({x / 1000:.1f}, {z / 1000:.1f}) km, precipitation {p:g} mm/h, fog {f:g}")
    names = ({4: "t4 noise", 5: "t5 weather map 0", 6: "t6 weather map 1", 7: "t7 coverage curve",
              2: "t2 precipitation 0", 3: "t3 precipitation 1"} if d["textures"] else
             {4: "t2 noise", 7: "t3 coverage curve", 2: "t4 2D map"})
    for reg, dims in d["dims"].items():
        if d["has_noise"] and reg in names and any(dims):
            print(f"  {names[reg]}: {dims[0]} x {dims[1]} x {dims[2]}, {dims[3]} levels")
    C = d["maps"][0] * (1 - Pc[6, 0]) + d["maps"][1] * Pc[6, 0]
    if not d["textures"]:
        C = np.zeros_like(C)  # (the layer generator has no weather map)
    covC = C > 0.01
    cov_sub, B_sub = coverage(d["volsub"])
    cov_c, B_c = coverage(d["vol"])
    sub_n = min(P.CG_VOL_N, (n + 3) // 4)
    cov_sub, B_sub = cov_sub[:sub_n, :sub_n], B_sub[:sub_n, :sub_n]
    print("\nslice  altitude   map cloudy  map mean C  volume cloudy  mean B in cloud")
    for k in range(K):
        inside = cov_sub[..., k] > 0.5
        print(f"{k:5d}  {CENTRES[k]:7.0f} m  {covC[..., k].mean() * 100:9.1f} %  {C[..., k][covC[..., k]].mean() if covC[..., k].any() else 0:10.3f}"
              f"  {inside.mean() * 100:11.1f} %  {B_sub[..., k][inside].mean() if inside.any() else 0:13.2e}")

    fig = plt.figure(figsize=(20, 20))
    gs = fig.add_gridspec(4, 4)
    # the generator's input: the weather map, or the layers
    if d["layers"]:
        ax = fig.add_subplot(gs[0, 0])
        for j in range(3):
            lo_m, hi_m = U[6, j], max(U[7, j], U[6, j] + 1)
            ax.bar(j, (hi_m - lo_m) / 1000, bottom=lo_m / 1000, color=plt.cm.Blues(0.3 + 0.7 * min(U[8, j], 1)))
            ax.text(j, hi_m / 1000 + 0.2, f"cov {U[8, j]:.2f}\ndens {U[9, j]:.2f}\nmix {np.round(U[3 + j], 2)}",
                    ha="center", fontsize=7)
        ax.set_xticks(range(3))
        ax.set_ylim(0, 16)
        ax.set_ylabel("altitude (km)")
        ax.set_title("layer generator: the three layers", fontsize=9)
        if d["map2d"]:
            ax = fig.add_subplot(gs[0, 1])
            ax.imshow(np.concatenate([d["g4"][..., c] for c in range(4)], 1), origin="lower", cmap="gray")
            ax.set_title("2D map t4: channels x y z w", fontsize=9)
    else:
        top, base = top_base(covC)
        ax = fig.add_subplot(gs[0, 0])
        im = ax.imshow(top, origin="lower", cmap="viridis", vmin=0, vmax=15)
        ax.set_title("weather map: highest cloudy slice (km)", fontsize=9)
        fig.colorbar(im, ax=ax, shrink=0.8)
        ax = fig.add_subplot(gs[0, 1])
        im = ax.imshow(base, origin="lower", cmap="viridis", vmin=0, vmax=15)
        ax.set_title("weather map: lowest cloudy slice (km)", fontsize=9)
        fig.colorbar(im, ax=ax, shrink=0.8)
    ax = fig.add_subplot(gs[0, 2])
    ax.plot(covC.mean((0, 1)) * 100, CENTRES / 1000, label="map: C > 0.01 (% of texels)")
    ax.plot(cov_sub.mean((0, 1)) * 100, CENTRES / 1000, label="volume: covered (% of columns)")
    ax.plot(C.mean((0, 1)) * 100, CENTRES / 1000, "--", label="map: mean C x 100")
    ax.set_ylim(0, 16)
    ax.set_ylabel("altitude (km MSL)")
    ax.legend(fontsize=8)
    ax.set_title("cloud amount by altitude", fontsize=9)
    if d["textures"]:
        ax = fig.add_subplot(gs[0, 3])
        mid = C.shape[0] // 2
        section(ax, C[mid], (0, 1), "weather map C, section through the middle row", vmax=1, xlabel="across the map")
    # the volume, whole disc (every 4th column)
    span = sub_n * 4 * s / max(n, 1) / 1000
    vtop, vbase = top_base(cov_sub > 0.5)
    ax = fig.add_subplot(gs[1, 0])
    im = ax.imshow(vtop, origin="lower", cmap="viridis", vmin=0, vmax=15)
    ax.set_title("volume (every 4th column): cloud tops (km)", fontsize=9)
    fig.colorbar(im, ax=ax, shrink=0.8)
    ax = fig.add_subplot(gs[1, 1])
    im = ax.imshow(vbase, origin="lower", cmap="viridis", vmin=0, vmax=15)
    ax.set_title("volume (every 4th column): cloud bases (km)", fontsize=9)
    fig.colorbar(im, ax=ax, shrink=0.8)
    ax = fig.add_subplot(gs[1, 2])
    section(ax, cov_sub[sub_n // 2], (-span / 2, span / 2),
            "volume coverage, section through the centre (whole disc, warped x)", vmax=1)
    ax = fig.add_subplot(gs[1, 3])
    im = section(ax, B_sub[sub_n // 2] * (cov_sub[sub_n // 2] > 0.5), (-span / 2, span / 2),
                 "volume extinction profile B (in cloud), same section", cmap="magma")
    fig.colorbar(im, ax=ax, shrink=0.8)
    # the volume, full resolution around the centre
    w = P.CG_VOL_N * s / max(n, 1) / 1000
    ctop, cbase = top_base(cov_c > 0.5)
    ax = fig.add_subplot(gs[2, 0])
    im = ax.imshow(ctop, origin="lower", cmap="viridis", vmin=0, vmax=15, extent=(-w / 2, w / 2, -w / 2, w / 2))
    ax.set_title(f"centre {w:.0f} km, full resolution: cloud tops (km)", fontsize=9)
    fig.colorbar(im, ax=ax, shrink=0.8)
    ax = fig.add_subplot(gs[2, 1])
    section(ax, cov_c[P.CG_VOL_N // 2], (-w / 2, w / 2), "centre: coverage, section through the centre", vmax=1)
    ax = fig.add_subplot(gs[2, 2])
    if d["has_noise"]:
        nz = d["noise"]
        tile = np.concatenate([np.concatenate([nz[:, :, k, c] for c in range(4)], 1) for k in (2, 12, 24)], 0)
        ax.imshow(tile, origin="lower", cmap="gray", vmin=0, vmax=1)
        ax.set_title(f"shape noise {'t4' if d['textures'] else 't2'}: channels x y z w (columns), layers 2, 12, 24 "
                     "(rows), 128 texels", fontsize=9)
    ax = fig.add_subplot(gs[2, 3])
    if d["has_noise"]:
        lw = min(d["dims"][7][0], P.CG_LUT_N)
        ax.plot(np.arange(lw) / max(lw - 1, 1), d["lut"][:lw], label="coverage curve (C -> LUT)")
        if d["textures"]:
            ax.plot(np.arange(lw) / max(lw - 1, 1), 1.3 * np.clip(d["lut"][:lw], 0, 1) - 0.25,
                    label="cloud threshold")
        ax.set_xlabel("weather map value C" if d["textures"] else "layer coverage")
        ax.legend(fontsize=8)
    if d["textures"]:
        ins = ax.inset_axes([0.55, 0.08, 0.42, 0.42])
        pr = d["precip"][0] * (1 - Pc[6, 1]) + d["precip"][1] * Pc[6, 1]
        ins.imshow(pr, origin="lower", cmap="Blues")
        ins.set_title(f"precipitation map (max {pr.max():.1f})", fontsize=7)
        ins.set_xticks([])
        ins.set_yticks([])
    # per column of the centre window: the anchor's weight, terrain
    col = d["columns"]
    for i, (k, title, cmap) in enumerate(((2, "METAR anchor weight (centre window)", "viridis"),
                                          (0, "local terrain t1 (m)", "terrain"),
                                          (1, "global terrain t0 (m)", "terrain"))):
        ax = fig.add_subplot(gs[3, i])
        im = ax.imshow(col[..., k], origin="lower", cmap=cmap, extent=(-w / 2, w / 2, -w / 2, w / 2))
        ax.set_title(title, fontsize=9)
        fig.colorbar(im, ax=ax, shrink=0.8)
    ax = fig.add_subplot(gs[3, 3])
    mid = P.CG_VOL_N // 2
    near = (cov_c[mid - 8:mid + 8, mid - 8:mid + 8] > 0.5).mean((0, 1)) * 100
    ax.barh(CENTRES / 1000, near, height=np.diff(EDGES) / 1000 * 0.9)
    ax.set_ylim(0, 16)
    ax.set_xlabel("% of columns covered")
    ax.set_title("covered within ~5 km of the centre, by altitude", fontsize=9)
    fig.suptitle(f"weather volume capture {args.capture}", fontsize=12)
    fig.tight_layout()
    out = CAPTURES / f"{args.capture}_clouds.png"
    fig.savefig(out, dpi=80)
    print("\nwrote", out)


if __name__ == "__main__":
    main()

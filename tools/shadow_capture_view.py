"""Render a shadow capture's images (patch_shadow_capture, DebugCapture bit 3).

    python tools/shadow_capture_view.py 14        # build/logs/captures/14_*

Writes <id>_shadow.png beside the capture:
- the 4 sun cascades whole, each pixel the highest caster of 8 x 8 texels, as
  metres along the light from the camera (+ = toward the sun; white = empty),
  with the camera's position marked;
- the screen's sun visibility as ch_deferred_shadows computed it (black =
  shadowed), and the outputs of passes patched with patch_output_capture
  (ch_raymarched_shadows ...), every 8th pixel.
"""

import argparse
import re
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CAPTURES = ROOT / "build" / "logs" / "captures"
IMAGE = 192


def load(capture: str) -> dict:
    txt_path = CAPTURES / f"{capture}_sky_atmosphere.txt"
    txt = txt_path.read_text().split("Sun shadow maps", 1)[1]
    rows = {}
    for m in re.finditer(r"^c(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)", txt, re.M):
        rows[int(m[1])] = np.array([float(m[i]) for i in range(2, 6)])
    maps = np.fromfile(CAPTURES / f"{capture}_shadow_maps.f32", np.float32).reshape(4, IMAGE, IMAGE, 2)
    screens = []  # (title, image): this pass's output, then other passes' (patch_output_capture)
    m = re.search(r"screen sun visibility: (\S+) \((\d+) x (\d+) float32", txt)
    if m:
        screens.append(("ch_deferred_shadows output: sun visibility from the sun's maps",
                        np.fromfile(CAPTURES / m[1], np.float32).reshape(int(m[3]), int(m[2]))))
    for m in re.finditer(r"^(\w+) output: (\S+) \((\d+) x (\d+) float32", txt, re.M):
        screens.append((f"{m[1]} output", np.fromfile(CAPTURES / m[2], np.float32).reshape(int(m[4]), int(m[3]))))
    window = None
    m = re.search(r"cascade (\d+), window ([\d.]+) m.*?level 0: (\S+) \((\d+) x", txt, re.S)
    if m and (CAPTURES / m[3]).exists():
        n = int(m[4])
        window = (int(m[1]), float(m[2]), np.fromfile(CAPTURES / m[3], np.float32).reshape(n, n))
    aircraft = re.search(r"^Aircraft \(SimConnect\): (.*)$", txt, re.M)
    return {"rows": rows, "maps": maps, "screens": screens, "window": window, "aircraft": aircraft[1] if aircraft else ""}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("capture", help="capture id (the number in build/logs/captures/<id>_*)")
    args = ap.parse_args()
    cap = load(args.capture)
    rows, maps = cap["rows"], cap["maps"]
    size = rows[40][0]

    panels = 4 + max(len(cap["screens"]), 1) + (cap["window"] is not None) + 1
    nrows = (panels + 2) // 3
    fig, axes = plt.subplots(nrows, 3, figsize=(18, 6 * nrows))
    for k in range(4):
        ax = axes.flat[k]
        u, v, d = rows[5 * k], rows[5 * k + 1], rows[5 * k + 2]
        per_m = np.linalg.norm(d[:3])
        metres = np.where(maps[k, :, :, 0] > 0, (maps[k, :, :, 0] - d[3]) / per_m, np.nan)
        finite = metres[np.isfinite(metres)]
        lim = max(np.percentile(np.abs(finite), 98), 1.0) if finite.size else 1.0
        cover = 1.0 / np.linalg.norm(u[:3])
        im = ax.imshow(metres, cmap="coolwarm", vmin=-lim, vmax=lim, extent=(0, cover, cover, 0))
        ax.plot(u[3] * cover, v[3] * cover, "k+", ms=14, mew=2)
        ax.set_title(f"cascade {k}: {cover:.0f} m wide, {cover / IMAGE:.2f} m per pixel\n"
                     "m along the light from the camera, + = sunward")
        ax.set_xlabel("m (u)")
        ax.set_ylabel("m (v)")
        fig.colorbar(im, ax=ax, shrink=0.8)
    for i, (title, image) in enumerate(cap["screens"] or [("screen outputs: not captured", None)]):
        ax = axes.flat[4 + i]
        if image is not None:
            ax.imshow(image, cmap="gray", vmin=0, vmax=1)
        ax.set_title(f"{title}\n(every 8th pixel, black = 0)")
    if cap["window"] is not None:
        k, w, cov = cap["window"]
        ax = axes.flat[panels - 2]
        ax.imshow(cov, cmap="gray_r", vmin=0, vmax=1, extent=(-w / 2, w / 2, w / 2, -w / 2))
        ax.plot(0, 0, "r+", ms=14, mew=2)
        ax.set_title(f"aircraft shadow window for the clouds (cascade {k}, {w:.0f} m)\n"
                     "coverage, black = caster; + = camera")
        ax.set_xlabel("m (u)")
        ax.set_ylabel("m (v)")
    for ax in axes.flat[panels - 1:]:
        ax.axis("off")
    axes.flat[panels - 1].text(0, 0.5, f"capture {args.capture}\nmap {size:.0f}^2 per cascade\n{cap['aircraft']}",
                      fontsize=11, va="center", wrap=True)
    fig.tight_layout()
    out = CAPTURES / f"{args.capture}_shadow.png"
    fig.savefig(out, dpi=90)
    print(out)


if __name__ == "__main__":
    main()

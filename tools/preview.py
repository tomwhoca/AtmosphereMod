"""Render a cockpit-style preview from LUTs dumped by atmolab, stock vs ours.

    build/lab/atmolab.exe dump build/preview/fl350 --model --sun 25 --alt 10700 --pitch -12 --apdist 150000
    python tools/preview.py build/preview/fl350 --sun 25 --alt 10700 --pitch -12 --apdist 150000

Rebuilds what the game does with the LUTs: sky pixels from the sky-view LUT,
terrain (a sphere with procedural field albedo) lit through the transmittance
LUT and hazed by the aerial-perspective volume (colour * (1 - a) + inscatter).
No clouds, sun disk or terrain relief. Use the same arguments as the dump.
Writes <dir>/preview.png.
"""

import argparse
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent


def read_pfm(path: Path) -> np.ndarray:
    with open(path, "rb") as f:
        kind = f.readline().strip()
        w, h = map(int, f.readline().split())
        scale = float(f.readline())
        data = np.frombuffer(f.read(), dtype="<f4" if scale < 0 else ">f4")
    channels = 3 if kind == b"PF" else 1
    return data.reshape(h, w, channels)[::-1].copy()  # PFM is bottom-up


def load_constants() -> np.ndarray:
    newest = max((ROOT / "build" / "logs" / "captures").glob("*.txt"), key=lambda p: p.stat().st_mtime)
    regs = np.zeros(52, np.float32)
    for m in re.finditer(r"^c(\d+)\s*\.([xyzw])\s+(\S+)", newest.read_text(), re.M):
        regs[int(m[1]) * 4 + "xyzw".index(m[2])] = float(m[3])
    return regs


def bilinear(tex: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Sample tex[h, w, c] at texel-centre coordinates x, y (clamped)."""
    h, w = tex.shape[:2]
    x = np.clip(x, 0, w - 1)
    y = np.clip(y, 0, h - 1)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]
    top = tex[y0, x0] * (1 - fx) + tex[y0, x1] * fx
    bottom = tex[y1, x0] * (1 - fx) + tex[y1, x1] * fx
    return top * (1 - fy) + bottom * fy


def transmittance_uv(c, r, mu):
    bottom2, top2, H, top = c[33], c[34], c[35], c[15]
    rho = np.sqrt(np.maximum(0, r * r - bottom2))
    d = np.maximum(0, -r * mu + np.sqrt(np.maximum(0, r * r * (mu * mu - 1) + top2)))
    d_min, d_max = top - r, rho + H
    return (d - d_min) / (d_max - d_min) * 255, rho / H * 63


def render(lut_dir: Path, prefix: str, args, c, size=(768, 512)) -> np.ndarray:
    sky = read_pfm(lut_dir / f"{prefix}_ch_sky_view.pfm")
    trans = read_pfm(lut_dir / f"{prefix}_ch_transmittance_lut.pfm")
    ap_rgb = read_pfm(lut_dir / f"{prefix}_ch_camera_volume.pfm")
    ap_a = read_pfm(lut_dir / f"{prefix}_ch_camera_volume_alpha.pfm")
    ap = np.concatenate([ap_rgb, ap_a], axis=2).reshape(64, 32, 32, 4)  # [slice, y, x, rgba]

    bottom = c[11]
    w, h = size
    u = (np.arange(w) + 0.5) / w
    v = (np.arange(h) + 0.5) / h
    uu, vv = np.meshgrid(u, v)
    view = np.stack([(uu * 2 - 1) * 1.0, (1 - vv * 2) * 0.577, np.ones_like(uu)], -1)
    p = np.radians(args.pitch)
    rows = np.array([[1, 0, 0], [0, np.cos(p), np.sin(p)], [0, -np.sin(p), np.cos(p)]])
    d = view @ rows
    d /= np.linalg.norm(d, axis=-1, keepdims=True)

    cam = np.array([0.0, bottom + args.alt / 1000, 0.0])
    s = np.radians(args.sun)
    sun = np.array([np.cos(s), np.sin(s), 0.0])

    # Ground hit on the planet sphere.
    b = d @ cam
    disc = b * b - (cam @ cam - bottom * bottom)
    t_ground = np.where(disc > 0, -b - np.sqrt(np.maximum(disc, 0)), np.inf)
    hit = np.isfinite(t_ground) & (t_ground > 0)

    # Sky: inverse of the MSFS sky-view mapping (zenith..horizon rows, full azimuth).
    frame = c[36:48].reshape(3, 4)[:, :3]
    local = d @ frame.T
    r = cam[1]
    cos_beta = np.sqrt(r * r - bottom * bottom) / r
    vzc = np.maximum(local[..., 1], -cos_beta)
    vrow = 1 - np.sqrt(np.clip(1 - (1 - vzc) / (1 + cos_beta), 0, 1))
    sin_z = np.sqrt(np.maximum(1 - vzc * vzc, 1e-8))
    cos_az = np.clip(-local[..., 2] / sin_z, -1, 1)
    a = np.sign(local[..., 0] + 1e-9) * np.sqrt((cos_az + 1) / 2)
    colour = bilinear(sky, (a + 1) / 2 * 127, vrow * 127)

    # Terrain: procedural field albedo, sun through the transmittance LUT,
    # crude sky ambient, then the aerial-perspective volume.
    gp = cam + d * np.where(hit, t_ground, 0)[..., None]
    up = gp / np.linalg.norm(gp, axis=-1, keepdims=True)
    mu = np.clip(up @ sun, -1, 1)
    tx, ty = transmittance_uv(c, np.full_like(mu, bottom + 0.01), mu)
    sun_t = bilinear(trans, tx, ty)
    fields = (np.sin(gp[..., 0] * 7.1) * np.sin(gp[..., 2] * 5.3) > 0)[..., None]
    noise = (np.sin(gp[..., 0] * 31.0 + gp[..., 2] * 17.0) * 0.5 + 0.5)[..., None]
    albedo = np.where(fields, [0.10, 0.14, 0.06], [0.16, 0.13, 0.09]) * (0.75 + 0.5 * noise)
    ambient = sky[:64].mean(axis=(0, 1)) * 0.6
    ground = albedo * (sun_t * np.maximum(mu, 0)[..., None] / np.pi + ambient)

    dist = np.where(hit, t_ground, 0)
    slice_f = np.sqrt(np.clip(dist * 1000 / args.apdist, 0, 1)) * 64 - 0.5
    ap_s = sample_ap(ap, uu * 32 - 0.5, vv * 32 - 0.5, slice_f)
    ground = ground * (1 - ap_s[..., 3:4]) + ap_s[..., :3]

    return np.where(hit[..., None], ground, colour)


def sample_ap(ap, ax, ay, slice_f):
    z0 = np.clip(np.floor(slice_f), 0, 63).astype(int)
    z1 = np.minimum(z0 + 1, 63)
    fz = np.clip(slice_f - np.floor(slice_f), 0, 1)[..., None]
    x = np.clip(ax, 0, 31)
    y = np.clip(ay, 0, 31)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    x1, y1 = np.minimum(x0 + 1, 31), np.minimum(y0 + 1, 31)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]

    def plane(z):
        top = ap[z, y0, x0] * (1 - fx) + ap[z, y0, x1] * fx
        bot = ap[z, y1, x0] * (1 - fx) + ap[z, y1, x1] * fx
        return top * (1 - fy) + bot * fy

    return plane(z0) * (1 - fz) + plane(z1) * fz


def tonemap(img: np.ndarray, exposure: float) -> np.ndarray:
    x = 1 - np.exp(-img * exposure)
    return (np.clip(x, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dir", type=Path)
    ap.add_argument("--sun", type=float, default=20)
    ap.add_argument("--alt", type=float, default=10000)
    ap.add_argument("--pitch", type=float, default=-10)
    ap.add_argument("--apdist", type=float, default=100000)
    ap.add_argument("--exposure", type=float, default=7.0)
    args = ap.parse_args()

    c = load_constants()
    panels = []
    for prefix, label in (("stock", "MSFS stock"), ("ours", "AtmosphereMod model")):
        img = Image.fromarray(tonemap(render(args.dir, prefix, args, c), args.exposure))
        ImageDraw.Draw(img).text((10, 8), label, fill=(255, 255, 255))
        panels.append(img)
    out = Image.new("RGB", (panels[0].width * 2 + 8, panels[0].height), (0, 0, 0))
    out.paste(panels[0], (0, 0))
    out.paste(panels[1], (panels[0].width + 8, 0))
    out.save(args.dir / "preview.png")
    print(f"-> {args.dir / 'preview.png'}")


if __name__ == "__main__":
    main()

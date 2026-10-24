"""Summarise what the add-on logged in game (build/logs).

    python tools/report.py

Lists which atmosphere variants are live at the
current settings, their dispatch sizes (and so texture resolutions), their
root-signature layouts, shaders not found in the pack, and the latest
constant capture. For the weather radar: the weather
volume's dispatches (ch_cloudDensity writes one texel column per thread, so its
size is the volume's N x N), radar draws and their viewports, and the 3D
textures the game created.
"""

import csv
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "build" / "logs"

# DXGI_FORMAT values likely for volumes (textures3d.tsv logs the number).
DXGI_FORMATS = {
    2: "R32G32B32A32_FLOAT", 9: "R16G16B16A16_TYPELESS", 10: "R16G16B16A16_FLOAT", 11: "R16G16B16A16_UNORM",
    24: "R10G10B10A2_TYPELESS", 25: "R10G10B10A2_UNORM", 26: "R11G11B10_FLOAT", 27: "R8G8B8A8_TYPELESS",
    28: "R8G8B8A8_UNORM", 33: "R16G16_TYPELESS", 34: "R16G16_FLOAT", 35: "R16G16_UNORM", 39: "R32_TYPELESS",
    41: "R32_FLOAT", 48: "R8G8_TYPELESS", 49: "R8G8_UNORM", 53: "R16_TYPELESS", 54: "R16_FLOAT",
    56: "R16_UNORM", 60: "R8_TYPELESS", 61: "R8_UNORM", 87: "B8G8R8A8_UNORM",
}
USAGE_FLAGS = {0x4: "rt", 0x8: "uav", 0xC0: "srv", 0x400: "copy_dst", 0x800: "copy_src"}


def main() -> None:
    if not (LOGS / "pipelines.tsv").exists():
        raise SystemExit(f"no logs in {LOGS}; run the sim with the add-on first")

    threads = {r["crc32"]: r["threads"] for r in csv.DictReader(open(ROOT / "extracted" / "index.csv"))}
    pipelines = list(csv.DictReader(open(LOGS / "pipelines.tsv"), delimiter="\t"))
    unknown = [p for p in pipelines if p["entry"] == "?"]
    print(f"{len(pipelines)} distinct shaders seen, {len(unknown)} not in the shader pack")
    for p in unknown[:20]:
        print(f"  unknown {p['stage']} {p['crc32']}")

    print("\nWatched dispatches (groups x numthreads = threads) and draws:")
    by_entry = defaultdict(list)
    for d in csv.DictReader(open(LOGS / "dispatches.tsv"), delimiter="\t"):
        by_entry[d["entry"]].append(d)
    for entry in sorted(by_entry):
        print(f"  {entry}")
        for d in by_entry[entry]:
            if d["groups_x"] == "indirect":
                print(f"    {d['crc32']}  indirect ({d['groups_y']} dispatch(es) per call)")
                continue
            if d["groups_x"].startswith("draw"):  # graphics pipeline, replaced or watched
                print(f"    {d['crc32']}  {d['groups_x']} {d['groups_y']} {d['groups_z']}".rstrip())
                continue
            groups = [int(d[k]) for k in ("groups_x", "groups_y", "groups_z")]
            tg = threads.get(d["crc32"], "")
            if tg:
                size = "x".join(str(g * int(t)) for g, t in zip(groups, tg.split("x")))
                print(f"    {d['crc32']}  groups {'x'.join(map(str, groups))}  numthreads {tg}  -> {size}")
            else:
                print(f"    {d['crc32']}  groups {'x'.join(map(str, groups))}")

    textures = LOGS / "textures3d.tsv"
    if textures.exists():
        rows = list(csv.DictReader(open(textures), delimiter="\t"))
        print(f"\n{len(rows)} distinct 3D texture(s); the weather volume is N x N x 32 with uav usage:")
        for t in rows:
            fmt = int(t["dxgi_format"])
            usage = int(t["usage"], 16)
            flags = ",".join(name for bit, name in USAGE_FLAGS.items() if (usage & bit) == bit) or t["usage"]
            print(f"  {t['width']}x{t['height']}x{t['depth']}  levels {t['levels']}  "
                  f"{DXGI_FORMATS.get(fmt, f'DXGI {fmt}')}  {flags}")

    layouts = LOGS / "layouts.txt"
    if layouts.exists() and layouts.stat().st_size:
        print("\nRoot signatures of watched passes:")
        print(layouts.read_text().rstrip())

    captures = sorted((LOGS / "captures").glob("*.txt")) if (LOGS / "captures").exists() else []
    print(f"\n{len(captures)} capture(s) in {LOGS / 'captures'}")
    if captures:
        print(captures[-1].read_text().rstrip())
    events = LOGS / "events.log"
    if events.exists():
        print("\nEvents:")
        print(events.read_text().rstrip())


if __name__ == "__main__":
    main()

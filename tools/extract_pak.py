"""Extract every DXIL shader from the MSFS 2024 shader pack.

The pack (Content/Shaders/SHADERSPAK.<hash>.SM6.PC.PAK) is an uncompressed
serialisation stream. Each shader is stored as a u32 length followed by a
standard DXBC container, so we carve on that rather than parsing the whole
stream. The entry-point name comes from the PSV0 chunk (runtime info v3).

Output layout:
    <out>/blobs/<entry>/<crc32>.<stage>_<maj>_<min>.cso
    <out>/index.csv

crc32 is over the full container, intended to match the CRC32 shader ids
RenoDX-style ReShade add-ons log (the add-on's logs use the same), so runtime
logs can be joined against index.csv.
"""

import argparse
import csv
import struct
import zlib
from pathlib import Path

DEFAULT_PAK = Path(r"D:\XboxGames\Microsoft Flight Simulator 2024\Content\Shaders")

# DXIL program kind (high 16 bits of ProgramVersion)
STAGES = {0: "ps", 1: "vs", 2: "gs", 3: "hs", 4: "ds", 5: "cs", 6: "lib",
          13: "ms", 14: "as"}


def find_pak(path: Path) -> Path:
    if path.is_file():
        return path
    paks = sorted(path.glob("SHADERSPAK.*.SM6.PC.PAK"))
    if not paks:
        raise SystemExit(f"no SHADERSPAK found in {path}")
    return paks[0]


def chunks(data: bytes, off: int) -> dict:
    n = struct.unpack_from("<I", data, off + 28)[0]
    out = {}
    for o in struct.unpack_from(f"<{n}I", data, off + 32):
        fourcc = data[off + o:off + o + 4].decode("ascii", "replace")
        size = struct.unpack_from("<I", data, off + o + 4)[0]
        out[fourcc] = (off + o + 8, size)
    return out


def parse_psv0(data: bytes, off: int, size: int) -> dict:
    """Return entry name and numthreads from a PSV0 chunk (v3 runtime info)."""
    end = off + size
    info_size = struct.unpack_from("<I", data, off)[0]
    info = off + 4
    p = info + info_size
    res_count = struct.unpack_from("<I", data, p)[0]
    p += 4
    if res_count:
        bind_size = struct.unpack_from("<I", data, p)[0]
        p += 4 + res_count * bind_size
    result = {"entry": "", "threads": "", "resources": res_count}
    if info_size < 36 or p + 4 > end:
        return result
    str_size = struct.unpack_from("<I", data, p)[0]
    strtab = data[p + 4:p + 4 + str_size]
    if info_size >= 48:
        tx, ty, tz = struct.unpack_from("<3I", data, info + 36)
        if tx:
            result["threads"] = f"{tx}x{ty}x{tz}"
    if info_size >= 52:
        name_off = struct.unpack_from("<I", data, info + 48)[0]
        result["entry"] = strtab[name_off:strtab.index(b"\0", name_off)].decode()
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pak", type=Path, default=DEFAULT_PAK,
                    help="pack file or the Shaders folder containing it")
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parent.parent / "extracted")
    ap.add_argument("--index-only", action="store_true",
                    help="write index.csv without dumping blobs")
    args = ap.parse_args()

    pak = find_pak(args.pak)
    data = pak.read_bytes()
    print(f"{pak.name}: {len(data):,} bytes")

    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    pos = 0
    while (i := data.find(b"DXBC", pos)) >= 0:
        size, nchunks = struct.unpack_from("<II", data, i + 24)
        if struct.unpack_from("<I", data, i - 4)[0] != size or not 0 < nchunks < 32:
            pos = i + 4
            continue
        blob = data[i:i + size]
        c = chunks(data, i)

        stage, sm = "?", "?"
        if "DXIL" in c:
            ver = struct.unpack_from("<I", data, c["DXIL"][0])[0]
            stage = STAGES.get(ver >> 16, f"k{ver >> 16}")
            sm = f"{(ver >> 4) & 0xF}_{ver & 0xF}"
        psv = parse_psv0(data, *c["PSV0"]) if "PSV0" in c else {}
        entry = psv.get("entry") or "unnamed"

        crc = f"{zlib.crc32(blob):08x}"
        rel = Path("blobs") / entry / f"0x{crc}.{stage}_{sm}.cso"
        if not args.index_only:
            dst = args.out / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(blob)

        rows.append({
            "index": len(rows), "offset": f"0x{i:x}", "size": size,
            "entry": entry, "stage": stage, "sm": sm, "crc32": f"0x{crc}",
            "dxbc_digest": blob[4:20].hex(), "threads": psv.get("threads", ""),
            "path": rel.as_posix(),
        })
        pos = i + size

    with open(args.out / "index.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    entries = {r["entry"] for r in rows}
    print(f"{len(rows)} shaders, {len(entries)} distinct entry points -> {args.out}")


if __name__ == "__main__":
    main()

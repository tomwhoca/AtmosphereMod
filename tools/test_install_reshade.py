"""Check how the installer finds ReShade (install.find_reshade): ReShade's own
dxgi.dll, ReShade64.dll loaded by another program (OptiShade), both, or
neither, in made-up sim folders; and install.product_name on real files.

    python tools/test_install_reshade.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import install  # noqa: E402

failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f": {detail}" if detail and not ok else ""))
    failures += not ok


def found(files: dict[str, str | None]):
    """find_reshade on a folder with these files, each with this product name."""
    with tempfile.TemporaryDirectory() as tmp:
        sim = Path(tmp)
        for name in files:
            (sim / name).write_bytes(b"")
        real = install.product_name
        install.product_name = lambda dll: files.get(dll.name)
        try:
            return install.find_reshade(sim)
        except install.InstallError as e:
            return e
        finally:
            install.product_name = real


def main():
    r = found({})
    check("no ReShade: error", isinstance(r, install.InstallError), repr(r))
    r = found({"dxgi.dll": "ReShade"})
    check("ReShade's dxgi.dll", r == ("dxgi.dll", None), repr(r))
    r = found({"dxgi.dll": "Something else"})
    check("another dxgi.dll alone: accepted as before", r == ("dxgi.dll", None), repr(r))
    for files in ({"ReShade64.dll": None, "winmm.dll": None},
                  {"ReShade64.dll": None, "dxgi.dll": "OptiShade"},
                  {"ReShade64.dll": None, "dxgi.dll": None}):
        r = found(files)
        check(f"loaded by another program, {sorted(files)} {list(files.values())}",
              not isinstance(r, Exception) and r[0].startswith("ReShade64.dll") and "reported" in r[1], repr(r))
    r = found({"ReShade64.dll": None, "dxgi.dll": "ReShade"})
    check("ReShade's dxgi.dll and a ReShade64.dll: warning",
          not isinstance(r, Exception) and r[0] == "dxgi.dll" and "twice" in r[1], repr(r))

    system = Path(r"C:\Windows\System32\version.dll")
    name = install.product_name(system)
    check("product name of version.dll", name is not None and "Windows" in name, repr(name))
    check("product name of a file without one", install.product_name(Path(__file__)) is None)
    check("product name of a missing file", install.product_name(ROOT / "missing.dll") is None)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()

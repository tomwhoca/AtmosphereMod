"""Check how the installer deals with ReShade (tools/install.py):

- find_reshade: ReShade's own dxgi.dll, ReShade64.dll loaded by another program (OptiShade), both, or neither,
  in made-up sim folders; and product_name on real files;
- reshade_limited: the standard build, which skips add-ons (issue #15; its DLL holds the message it logs when it
  does), as dxgi.dll or ReShade64.dll, against the build with full add-on support;
- addon_folder: [ADDON] AddonPath in ReShade.ini, absolute or relative, else the sim's folder (issue #15);
- install_addon: the add-on also lands where ReShade looks for it; a folder it can't write to is only a warning;
- uninstall: removes it from those folders too, and nothing else there.

    python tools/test_install_reshade.py
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import install  # noqa: E402

failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f": {detail}" if detail and not ok else ""))
    failures += not ok


def quiet(f, *args):
    """f's result and what it said."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = f(*args)
    return result, out.getvalue()


def new_sim(tmp: Path, name: str, ini: str | None = None) -> Path:
    sim = tmp / name
    sim.mkdir()
    (sim / install.EXE).write_bytes(b"")
    if ini is not None:
        (sim / "ReShade.ini").write_bytes(ini.encode("utf-8"))
    return sim


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


def finding() -> None:
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


def limited(tmp: Path) -> None:
    sim = new_sim(tmp, "limited")
    check("no ReShade: not limited", not install.reshade_limited(sim))
    (sim / "dxgi.dll").write_bytes(b"MZ... Searching for add-ons (*.addon, *.addon64) in '...")
    check("the build with full add-on support: not limited", not install.reshade_limited(sim))
    (sim / "dxgi.dll").write_bytes(b"MZ... because this build of ReShade has only limited add-on functionality. ...")
    check("the standard build as dxgi.dll: limited", install.reshade_limited(sim))
    (sim / "dxgi.dll").write_bytes(b"MZ... another program's loader")
    (sim / install.RESHADE_LIBRARY).write_bytes(b"... only limited add-on functionality ...")
    check("the standard build as ReShade64.dll: limited", install.reshade_limited(sim))


def folders(tmp: Path) -> None:
    other = tmp / "other add-ons"
    check("no ReShade.ini: the sim's folder", install.addon_folder(new_sim(tmp, "a")) == tmp / "a")
    check("no AddonPath: the sim's folder",
          install.addon_folder(new_sim(tmp, "b", "[ADDON]\r\nDisabledAddons=\r\n\r\n[GENERAL]\r\nx=1\r\n")) == tmp / "b")
    check("an empty AddonPath: the sim's folder",
          install.addon_folder(new_sim(tmp, "c", "[ADDON]\r\nAddonPath=\r\n")) == tmp / "c")
    check("AddonPath in another section doesn't count",
          install.addon_folder(new_sim(tmp, "d", f"[GENERAL]\nAddonPath={other}\n")) == tmp / "d")
    # (as in the reporter's log: an absolute path with a backslash at its end)
    check("an absolute AddonPath",
          install.addon_folder(new_sim(tmp, "e", f"[GENERAL]\r\nx=1\r\n[ADDON]\r\nAddonPath={other}\\\r\nY=2\r\n")) == other)
    check("spaces and quotes around it",
          install.addon_folder(new_sim(tmp, "f", f'[ADDON]\nAddonPath = "{other}" \n')) == other)
    check("a relative AddonPath: from the sim's folder",
          install.addon_folder(new_sim(tmp, "g", "[ADDON]\r\nAddonPath=.\\addons\\\r\n")) == tmp / "g" / "addons")


def addon(tmp: Path) -> None:
    payload = tmp / "payload"
    payload.mkdir()
    (payload / install.ADDON).write_bytes(b"addon")

    sim = new_sim(tmp, "plain", "[ADDON]\r\n")
    folder, said = quiet(install.install_addon, payload, sim)
    check("default: in the sim's folder, nothing to say",
          folder == sim and (sim / install.ADDON).exists() and said == "", said)

    same = new_sim(tmp, "same", f"[ADDON]\r\nAddonPath={tmp / 'same'}\\\r\n")
    folder, said = quiet(install.install_addon, payload, same)
    check("AddonPath naming the sim's folder: nothing to say", (same / install.ADDON).exists() and said == "", said)

    other = tmp / "pack"
    other.mkdir()
    (other / "zzz_other.addon64").write_bytes(b"theirs")
    sim = new_sim(tmp, "moved", f"[ADDON]\r\nAddonPath={other}\\\r\n")
    folder, said = quiet(install.install_addon, payload, sim)
    check("AddonPath elsewhere: there too, and it says so",
          folder == other and (other / install.ADDON).read_bytes() == b"addon" and (sim / install.ADDON).exists()
          and str(other) in said, said)
    check("... the folder's own add-ons stay", (other / "zzz_other.addon64").read_bytes() == b"theirs")

    gone = tmp / "made" / "for" / "it"
    sim2 = new_sim(tmp, "created", f"[ADDON]\r\nAddonPath={gone}\r\n")
    quiet(install.install_addon, payload, sim2)
    check("a missing folder is made", (gone / install.ADDON).exists())

    # a file where the folder should be: can't be written to, a warning and no crash
    blocked = tmp / "blocked"
    blocked.write_bytes(b"a file")
    sim3 = new_sim(tmp, "cant", f"[ADDON]\r\nAddonPath={blocked}\r\n")
    folder, said = quiet(install.install_addon, payload, sim3)
    check("a folder it can't write to: a warning naming what to do",
          said.startswith("Warning") and str(sim3 / install.ADDON) in said and (sim3 / install.ADDON).exists(), said)

    # uninstall. (community: a folder of the test's, so it can't reach the real one)
    data = tmp / "data"
    data.mkdir()
    (tmp / "community").mkdir()
    (data / "install.json").write_text(json.dumps({"sim": str(sim), "addons": str(tmp / "earlier")}))
    (tmp / "earlier").mkdir()
    (tmp / "earlier" / install.ADDON).write_bytes(b"addon")
    real_running = install.sim_running
    install.sim_running = lambda: False
    try:
        quiet(install.uninstall, SimpleNamespace(data=data, sim=sim, community=tmp / "community", yes=True, uninstall=True))
    finally:
        install.sim_running = real_running
    check("uninstall: removed from the sim's folder, AddonPath's and the one at the install",
          not (sim / install.ADDON).exists() and not (other / install.ADDON).exists()
          and not (tmp / "earlier" / install.ADDON).exists())
    check("... the folder's own add-ons stay", (other / "zzz_other.addon64").exists())
    check("... AddonPath stays in ReShade.ini", "AddonPath" in (sim / "ReShade.ini").read_text())


def main() -> int:
    finding()
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        for name, f in (("limited", limited), ("folders", folders), ("addon", addon)):
            (tmp / name).mkdir()
            f(tmp / name)
    print("all checks passed" if not failures else f"{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

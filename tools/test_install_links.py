"""Check the installer's Community folder handling (tools/install.py):

- remove_package: a real package folder is removed; a junction or a symbolic
  link (as add-on linkers make; issues #8 and #10) is removed as the link
  only, the folder it points to and its files stay; also a link whose folder
  is gone.
- install_efb: the new app lands as a real folder (fresh, over a folder, over
  a link); after a link the user is told, with the linked folder named, that
  it still holds the old version.
- community_folder(sim): the UserCfg.opt of the sim's own version first
  (Steam for a sim in a Steam library, else MS Store), the other as fallback.

    python tools/test_install_links.py
"""
import os
import shutil
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


def junction(link: Path, target: Path) -> None:
    import _winapi
    _winapi.CreateJunction(str(target), str(link))


def package(folder: Path, text: str) -> Path:
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(text)
    return folder


def links(tmp: Path) -> None:
    community = tmp / "Community"
    community.mkdir()
    payload = package(tmp / "payload" / install.EFB, "new")

    # a real folder: removed, then the new package
    package(community / install.EFB, "old")
    linked = install.remove_package(community / install.EFB)
    check("a real folder is removed", linked is None and not (community / install.EFB).exists())

    # a junction to a package elsewhere (an add-on linker's)
    kept = package(tmp / "Addons" / install.EFB, "linked")
    junction(community / install.EFB, kept)
    check("a junction is a link", install.is_link(community / install.EFB))
    linked = install.remove_package(community / install.EFB)
    check("a junction goes as the link", not os.path.lexists(community / install.EFB), repr(linked))
    check("... the folder it points to stays, with its files",
          (kept / "manifest.json").read_text() == "linked")
    check("... and is named", linked is not None and Path(linked) == kept, repr(linked))
    shutil.copytree(payload, community / install.EFB)
    check("then the EFB app's copy is a real folder", not install.is_link(community / install.EFB)
          and (community / install.EFB / "manifest.json").read_text() == "new")
    shutil.rmtree(community / install.EFB)

    # a junction whose folder is gone
    gone = package(tmp / "Gone" / install.EFB, "gone")
    junction(community / install.EFB, gone)
    shutil.rmtree(gone)
    check("a broken junction is a link", install.is_link(community / install.EFB)
          and not (community / install.EFB).exists())
    install.remove_package(community / install.EFB)
    check("... and is removed", not os.path.lexists(community / install.EFB))

    # a symbolic link to a folder (needs Developer Mode or admin rights)
    try:
        os.symlink(kept, community / install.EFB, target_is_directory=True)
    except OSError as e:
        print(f"skip a symbolic link: {e}")
    else:
        check("a symbolic link is a link", install.is_link(community / install.EFB))
        install.remove_package(community / install.EFB)
        check("a symbolic link goes as the link", not os.path.lexists(community / install.EFB))
        check("... its folder stays, with its files", (kept / "manifest.json").read_text() == "linked")

    check("a plain folder isn't a link", not install.is_link(kept))
    check("a missing path isn't a link", not install.is_link(tmp / "missing"))


def efb_step(tmp: Path) -> None:
    """install_efb: the new app as a real folder each time; the note only after a link."""
    community = tmp / "Community"
    community.mkdir()
    payload = tmp / "payload"
    package(payload / "efb" / install.EFB, "new")
    said = []
    real_say = install.say
    install.say = lambda text="": said.append(text)
    try:
        for case, before in (("a fresh install", None), ("over a real folder", "old"), ("over a junction", "link")):
            said.clear()
            library = None
            if os.path.lexists(community / install.EFB):
                shutil.rmtree(community / install.EFB)  # (the previous case's)
            if before == "old":
                package(community / install.EFB, "old")
            elif before == "link":
                library = package(tmp / "Library" / install.EFB, "old, linked")
                junction(community / install.EFB, library)
            install.install_efb(payload, community)
            note = any("Note:" in line for line in said)
            check(f"{case}: the new app, a real folder", not install.is_link(community / install.EFB)
                  and (community / install.EFB / "manifest.json").read_text() == "new")
            check(f"{case}: {'the note' if library else 'no note'}", note == bool(library), "\n".join(said))
            if library:
                check(f"{case}: the note names the linked folder", any(str(library) in line for line in said),
                      "\n".join(said))
                check(f"{case}: the linked folder keeps the old version",
                      (library / "manifest.json").read_text() == "old, linked")
    finally:
        install.say = real_say


def user_cfg(tmp: Path) -> None:
    local, roaming = tmp / "Local", tmp / "Roaming"
    store_cfg = local / "Packages/Microsoft.Limitless_8wekyb3d8bbwe/LocalCache/UserCfg.opt"
    steam_cfg = roaming / "Microsoft Flight Simulator 2024/UserCfg.opt"
    store_pkgs, steam_pkgs = tmp / "StorePackages", tmp / "SteamPackages"
    for cfg, pkgs in ((store_cfg, store_pkgs), (steam_cfg, steam_pkgs)):
        cfg.parent.mkdir(parents=True)
        pkgs.mkdir()
        cfg.write_text(f'Version 1\nInstalledPackagesPath "{pkgs}"\n')
    store_sim = Path(r"D:\XboxGames\Microsoft Flight Simulator 2024\Content")
    steam_sim = Path(r"E:\SteamLibrary\steamapps\common\Microsoft Flight Simulator 2024")
    saved = {k: os.environ[k] for k in ("LOCALAPPDATA", "APPDATA")}
    os.environ["LOCALAPPDATA"], os.environ["APPDATA"] = str(local), str(roaming)
    try:
        got = install.community_folder(steam_sim)
        check("a Steam sim: Steam's Community", got == steam_pkgs / "Community", str(got))
        got = install.community_folder(store_sim)
        check("an MS Store sim: the Store's Community", got == store_pkgs / "Community", str(got))
        got = install.community_folder()
        check("no sim given: the Store's first, as before", got == store_pkgs / "Community", str(got))
        steam_cfg.unlink()
        got = install.community_folder(steam_sim)
        check("a Steam sim without Steam's UserCfg.opt: the Store's", got == store_pkgs / "Community", str(got))
    finally:
        os.environ.update(saved)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        links(Path(tmp))
    with tempfile.TemporaryDirectory() as tmp:
        efb_step(Path(tmp))
    with tempfile.TemporaryDirectory() as tmp:
        user_cfg(Path(tmp))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()

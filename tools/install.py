"""AtmosphereMod installer: patches your own copy of MSFS 2024's shaders and installs the mod.

Run "Install AtmosphereMod.cmd" from the release folder (it starts this with
the bundled or the system Python), or:

    python install.py [--sim DIR] [--community DIR] [--data DIR] [--dxc DIR] [--yes]
    python install.py --uninstall

What it does, with the sim closed:
1. finds MSFS 2024 (MS Store / Xbox app or Steam), ReShade next to it, and the
   Community folder; it asks for the sim's or the Community folder if it
   can't find them;
2. finds Microsoft's DirectX Shader Compiler (the release's own copy in its
   dxc folder, else the Windows SDK's, else a copy it downloads once from Microsoft's
   GitHub release, after asking);
3. extracts the sim's shaders from its own shader pack into a work folder and
   patches them there (patch_dxil.py); targets the patcher doesn't recognise
   (after a sim update) are skipped and stay stock;
4. puts the patched shaders and tunables.ini into the data
   folder (%LOCALAPPDATA%\\AtmosphereMod by default; an existing tunables.ini is
   kept, settings new in this version are added to it);
5. installs the add-on next to ReShade, the EFB app into the Community folder
   and the [ATMOSPHEREMOD] settings into ReShade.ini (backed up first);
6. deletes the work folder.

The game's own files are never changed: the add-on swaps shaders in memory.
Run it again after every sim update. The release contains none of the game's
files; the patched shaders are made here, from this PC's installation.
"""

import argparse
import contextlib
import csv
import hashlib
import json
import os
import re
import shutil
import string
import subprocess
import sys
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

VERSION_FILE = HERE / "VERSION"
EXE = "FlightSimulator2024.exe"
MS_STORE_LIBRARY = "XboxGames"  # the Xbox app's default game folder on each drive
MS_STORE_FOLDER = Path("Microsoft Flight Simulator 2024") / "Content"  # in it
STEAM_FOLDER = Path("steamapps") / "common" / "Microsoft Flight Simulator 2024"
ADDON = "AtmosphereMod.addon64"
EFB = "atmospheremod-weather"
SECTION = "ATMOSPHEREMOD"
BACKUP = "ReShade.ini.atmospheremod.bak"
# Microsoft's DirectX Shader Compiler release the patcher was written against
# (the Windows SDK 10.0.26100's dxc is the same version). The disassembly's
# text format can change between versions, so this one is pinned.
DXC_VERSION = "1.8.2502"
DXC_URL = ("https://github.com/microsoft/DirectXShaderCompiler/releases/download/"
           "v1.8.2502/dxc_2025_02_20.zip")
DXC_SIZE = 26346851
# GitHub publishes no checksum: this one is from a download whose dxc.exe,
# dxcompiler.dll and dxil.dll carried valid Microsoft Corporation signatures
# (2026-10-23, build 1.8.2502.8). The installer checks both.
DXC_SHA256 = "70b1913a1bfce4a3e1a5311d16246f4ecdf3a3e613abec8aa529e57668426f85"
DXC_FILES = ("dxc.exe", "dxcompiler.dll", "dxil.dll")


DISCLAIMER = """\
AtmosphereMod is unofficial and experimental, provided as is, without any
warranty. It has been tested on a single PC: on other hardware or drivers it
may not work, may show graphical errors, may crash the sim or may cost
performance. You install and use it at your own risk (README.md, "Disclaimer").
It changes none of the game's files; Ctrl+Shift+F9 switches back to stock at
once, and "Uninstall AtmosphereMod.cmd" removes it."""


class InstallError(Exception):
    pass


def say(text: str = "") -> None:
    print(text, flush=True)


def ask(question: str, yes: bool, default: bool = True) -> bool:
    if yes:
        return True
    try:
        answer = input(f"{question} {'[Y/n]' if default else '[y/N]'} ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes", "j", "ja") or (default and answer == "")


def version() -> str:
    return VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else "dev"


def sim_running() -> bool:
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {EXE}", "/NH"], capture_output=True, text=True).stdout
    return EXE in out


# ---------------------------------------------------------------- finding things

def steam_libraries() -> list[Path]:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam = Path(winreg.QueryValueEx(key, "SteamPath")[0])
    except OSError:
        return []
    libs = [steam]
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(errors="ignore")):
            libs.append(Path(m[1].replace("\\\\", "\\")))
    return libs


def xbox_libraries() -> list[Path]:
    """The MS Store / Xbox app's game folders: <drive>:\\XboxGames, or the
    folder chosen in the Xbox app, which each drive's .GamingRoot names (an
    "RGBX" header, a count, then null-terminated UTF-16 paths relative to the
    drive)."""
    libs = []
    for d in string.ascii_uppercase:
        root = Path(f"{d}:\\")
        if not os.path.exists(root):
            continue
        libs.append(root / MS_STORE_LIBRARY)
        try:
            raw = (root / ".GamingRoot").read_bytes()
        except OSError:
            continue
        if raw[:4] == b"RGBX":
            for name in raw[8:].decode("utf-16-le", errors="ignore").split("\0"):
                if name.strip(" \\"):
                    libs.append(root / name.strip(" \\"))
    return list(dict.fromkeys(libs))


def find_sims() -> list[Path]:
    """Folders holding FlightSimulator2024.exe: in the MS Store / Xbox app's
    game folders (...\\Microsoft Flight Simulator 2024\\Content) and Steam's
    libraries (steamapps\\common\\Microsoft Flight Simulator 2024), also under
    another game folder name."""
    found = []
    for lib in xbox_libraries():
        found += [p for p in (lib / MS_STORE_FOLDER, *lib.glob("*/Content")) if (p / EXE).exists()]
    for lib in steam_libraries():
        found += [p for p in (lib / STEAM_FOLDER, *(lib / "steamapps" / "common").glob("*")) if (p / EXE).exists()]
    return list(dict.fromkeys(found))


def sim_folder(path: Path) -> Path | None:
    """The folder with FlightSimulator2024.exe for a path a user gives: that
    folder, the exe itself, or the game folder above Content."""
    if path.name.lower() == EXE.lower() and path.is_file():
        return path.parent
    for p in (path, path / "Content"):
        if (p / EXE).is_file():
            return p
    return None


def ask_sim_folder(yes: bool) -> Path | None:
    """Ask for the sim's folder until it holds FlightSimulator2024.exe; None
    when cancelled (empty answer) or not interactive."""
    if yes:
        return None
    say("MSFS 2024 wasn't found automatically. Enter the folder with FlightSimulator2024.exe")
    say("(where you installed ReShade), for example:")
    say(r"  D:\XboxGames\Microsoft Flight Simulator 2024\Content           (MS Store / Xbox app)")
    say(r"  C:\Program Files (x86)\Steam\steamapps\common\Microsoft Flight Simulator 2024   (Steam)")
    say("You can also drag FlightSimulator2024.exe into this window. Press Enter alone to cancel.")
    while True:
        try:
            answer = input("Folder: ").strip().strip('"').strip()
        except EOFError:
            return None
        if not answer:
            return None
        path = Path(os.path.expandvars(answer))
        found = sim_folder(path)
        if found:
            say()
            return found
        say(f"No {EXE} in {path}. Try again, or press Enter to cancel.")


def find_pak(where: Path) -> Path:
    """The shader pack: a file, or the newest SHADERSPAK in a folder or its Shaders folder."""
    if where.is_file():
        return where
    paks = sorted([*where.glob("SHADERSPAK.*.SM6.PC.PAK"), *(where / "Shaders").glob("SHADERSPAK.*.SM6.PC.PAK")],
                  key=lambda f: f.stat().st_mtime)
    if not paks:
        raise InstallError(f"no shader pack (Shaders\\SHADERSPAK.*.SM6.PC.PAK) in {where}")
    return paks[-1]


def reshade_version(sim: Path) -> str | None:
    """ReShade's version from its log (written on every start), if it has run."""
    log = sim / "ReShade.log"
    if log.exists():
        m = re.search(r"ReShade version '([\d.]+)'", log.read_text(errors="ignore"))
        if m:
            return m[1]
    return None


def community_folder() -> Path | None:
    """<InstalledPackagesPath>\\Community, from MSFS 2024's UserCfg.opt (MS Store or Steam)."""
    for cfg in (Path(os.environ["LOCALAPPDATA"]) / "Packages/Microsoft.Limitless_8wekyb3d8bbwe/LocalCache/UserCfg.opt",
                Path(os.environ["APPDATA"]) / "Microsoft Flight Simulator 2024/UserCfg.opt"):
        if cfg.exists():
            m = re.search(r'^InstalledPackagesPath\s+"([^"]+)"', cfg.read_text(errors="ignore"), re.M)
            if m:
                return Path(m[1]) / "Community"
    return None


def community_from(path: Path) -> Path | None:
    """The Community folder for a path a user gives: that folder, or the
    packages folder holding it."""
    for p in (path, path / "Community"):
        if p.is_dir() and p.name.lower() == "community":
            return p
    return None


def ask_community_folder(yes: bool, skip: str) -> Path | None:
    """Ask for the Community folder until it is one; None when skipped (empty
    answer) or not interactive. skip: what skipping means."""
    if yes:
        return None
    say("Your Community folder wasn't found automatically. Enter it: the folder named Community")
    say("where your add-ons are, usually")
    say(r"  %LOCALAPPDATA%\Packages\Microsoft.Limitless_8wekyb3d8bbwe\LocalCache\Packages\Community   (MS Store)")
    say(r"  %APPDATA%\Microsoft Flight Simulator 2024\Packages\Community                            (Steam)")
    say("unless you chose another place for your packages.")
    say(f"Press Enter alone to skip: {skip}.")
    while True:
        try:
            answer = input("Community folder: ").strip().strip('"').strip()
        except EOFError:
            return None
        if not answer:
            say()
            return None
        path = Path(os.path.expandvars(answer))
        found = community_from(path)
        if found:
            say()
            return found
        say(f"No Community folder at {path}. Try again, or press Enter to skip.")


def dxc_version(dxc: Path) -> str:
    out = subprocess.run([str(dxc), "--version"], capture_output=True, text=True).stdout
    m = re.search(r"dxcompiler\.dll: [\d.]+ - (\d+\.\d+\.\d+)", out)
    return m[1] if m else "?"


def signed_by_microsoft(folder: Path) -> bool:
    """dxc.exe, dxcompiler.dll and dxil.dll carry valid Microsoft Corporation
    Authenticode signatures (checked before running anything downloaded)."""
    script = ("$ok = $true; foreach ($f in @(" + ", ".join(f"'{folder / n}'" for n in DXC_FILES) + ")) { "
              "$s = Get-AuthenticodeSignature -LiteralPath $f; "
              "if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') "
              "{ $ok = $false } }; if ($ok) { 'valid' }")
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True).stdout
    return out.strip() == "valid"


def find_dxc(data: Path, given: Path | None, yes: bool) -> Path:
    """dxc.exe with dxcompiler.dll and dxil.dll beside it, of DXC_VERSION."""
    candidates = []
    if given:
        candidates.append(given / "dxc.exe" if given.is_dir() else given)
    for folder in (HERE / "dxc" / "bin" / "x64",   # the release's own copy
                   data / "dxc" / "bin" / "x64"):  # an earlier download
        if (folder / "dxc.exe").exists() and signed_by_microsoft(folder):
            candidates.append(folder / "dxc.exe")
    kits = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Windows Kits" / "10" / "bin"
    candidates += sorted(kits.glob("10.*/x64/dxc.exe"), reverse=True)
    others = []
    for c in candidates:
        if c.exists() and (c.parent / "dxcompiler.dll").exists() and (c.parent / "dxil.dll").exists():
            v = dxc_version(c)
            if v == DXC_VERSION:
                return c
            others.append((c, v))
    say(f"The patcher needs Microsoft's DirectX Shader Compiler {DXC_VERSION}"
        + (f" (found other versions: {', '.join(v for _, v in others)})" if others else "") + ".")
    if ask(f"Download it from Microsoft's GitHub release ({DXC_SIZE / 1e6:.0f} MB, once, into {data / 'dxc'})?",
           yes):
        return download_dxc(data)
    if others:
        say(f"Using {others[0][0]} ({others[0][1]}): some shaders may not patch with it.")
        return others[0][0]
    raise InstallError("no DirectX Shader Compiler: install the Windows SDK, or allow the download")


def download_dxc(data: Path) -> Path:
    target = data / "dxc"
    zip_path = data / "dxc.zip"
    data.mkdir(parents=True, exist_ok=True)
    say(f"Downloading {DXC_URL} ...")
    with urllib.request.urlopen(DXC_URL, timeout=60) as r, open(zip_path, "wb") as f:
        shutil.copyfileobj(r, f)
    size = zip_path.stat().st_size
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    if size != DXC_SIZE or digest != DXC_SHA256:
        zip_path.unlink()
        raise InstallError(f"the download doesn't match the release ({size} bytes, sha256 {digest})")
    if target.exists():
        shutil.rmtree(target)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(target)
    zip_path.unlink()
    dxc = target / "bin" / "x64" / "dxc.exe"
    if not dxc.exists() or not signed_by_microsoft(dxc.parent):
        shutil.rmtree(target, ignore_errors=True)
        raise InstallError("the downloaded compiler isn't signed by Microsoft: removed it")
    if dxc_version(dxc) != DXC_VERSION:
        raise InstallError(f"the downloaded compiler isn't version {DXC_VERSION}")
    say(f"DirectX Shader Compiler {DXC_VERSION} in {target} (checksum and Microsoft signatures verified)")
    return dxc


# ---------------------------------------------------------------- ReShade.ini

def read_ini(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig") if path.exists() else ""


def set_key(text: str, key: str, value: str) -> str:
    line = f"{key}={value}"
    section = re.search(rf"^\[{SECTION}\][^\[]*", text, re.M)
    if section is None:
        return text.rstrip("\n") + f"\n\n[{SECTION}]\n{line}\n"
    body = section.group(0)
    if re.search(rf"^{key}=.*$", body, re.M):
        body = re.sub(rf"^{key}=.*$", line.replace("\\", "\\\\"), body, flags=re.M)
    else:
        body = body.rstrip("\n") + f"\n{line}\n\n"
    return text[:section.start()] + body + text[section.end():]


def remove_section(text: str) -> str:
    return re.sub(rf"^\[{SECTION}\][^\[]*", "", text, flags=re.M)


# ---------------------------------------------------------------- tunables.ini

KEY = re.compile(r"^\s*([TRC]\d+)\s*=", re.M)


def merge_tunables(new: str, old: str) -> tuple[str, list[str]]:
    """The user's file, plus every setting of the new defaults it lacks (with
    the comment lines above it)."""
    have = set(KEY.findall(old))
    added, block, out = [], [], []
    for line in new.splitlines():
        m = KEY.match(line)
        if m is None:
            block = block + [line] if line.strip().startswith(";") else []
            continue
        if m[1] not in have:
            added.append(m[1])
            out += block + [line]
        block = []
    if not added:
        return old, []
    return (old.rstrip("\n") + f"\n\n; ---- added by the AtmosphereMod installer {version()} "
            f"({datetime.now():%Y-%m-%d}): settings new in this version\n" + "\n".join(out) + "\n"), added


# ---------------------------------------------------------------- the steps

def patch(pak: Path, dxc: Path, work: Path, shaders: Path, log) -> tuple[int, int, list[str]]:
    """Extract the pack into work/, patch, move the results into shaders/.
    Returns (patched, failed, crcs in the pack)."""
    if work.exists():
        shutil.rmtree(work)
    (work / "shaders").mkdir(parents=True)
    shutil.copy2(HERE / "manifest.json", work / "shaders" / "manifest.json")
    say(f"Extracting the sim's shaders from {pak.name} ...")
    res = subprocess.run([sys.executable, str(HERE / "extract_pak.py"), "--pak", str(pak), "--out",
                          str(work / "extracted")], capture_output=True, text=True)
    log.write(res.stdout + res.stderr)
    if res.returncode != 0:
        raise InstallError(f"extracting the shaders failed:\n{res.stderr.strip()}")
    say("Patching them (about a minute) ...")
    import patch_dxil
    with contextlib.redirect_stdout(log):
        failed = patch_dxil.patch_all(root=work, dxc=dxc, strict=False)
    made = sorted((work / "build" / "shaders").glob("*.cso"))
    for f in made:
        shutil.move(str(f), shaders / f.name)
    rows = list(csv.DictReader(open(work / "extracted" / "index.csv")))
    names = sorted({f"{r['crc32']}\t{r['entry']}" for r in rows})
    (shaders.parent / "shader_names.tsv").write_text("\n".join(names) + "\n")
    return len(made), failed, [r["crc32"] for r in rows]


def install(args) -> None:
    payload = args.payload or HERE.parent / "payload"
    if not (payload / ADDON).exists():
        raise InstallError(f"no {ADDON} in {payload}: run this from the release folder")
    if sim_running():
        raise InstallError("Microsoft Flight Simulator 2024 is running: close it first")

    if args.sim:
        sims = [sim_folder(args.sim)]
        if sims[0] is None:
            raise InstallError(f"no {EXE} in {args.sim}")
    else:
        sims = find_sims()
        if not sims:
            manual = ask_sim_folder(args.yes)
            sims = [manual] if manual else []
    if not sims:
        raise InstallError("MSFS 2024 not found; pass its folder (with FlightSimulator2024.exe) with --sim")
    if len(sims) > 1:
        say("Found several installations: " + ", ".join(map(str, sims)) + f"; using {sims[0]} (or pass --sim)")
    sim = sims[0]
    pak = find_pak(args.pak or sim)
    if not (sim / "dxgi.dll").exists():
        raise InstallError(f"ReShade isn't installed in {sim}: install ReShade 6.8 or newer *with full add-on "
                           f"support* for {EXE} first (https://reshade.me)")
    rv = reshade_version(sim)
    community = args.community or community_folder()
    if community is None:
        community = ask_community_folder(args.yes, "the EFB app, and with it the METAR features, is left out")
    data = args.data
    say(f"AtmosphereMod {version()}")
    say(f"  sim:        {sim}")
    say(f"  ReShade:    {rv or 'version unknown (start the sim once with ReShade first)'}")
    say(f"  shaders:    {pak}")
    say(f"  Community:  {community or 'not found: the EFB app (METAR features) is skipped'}")
    say(f"  data:       {data}")
    if rv and tuple(int(x) for x in rv.split(".")[:2]) < (6, 8):
        say(f"  ReShade {rv} is too old: install 6.8 or newer with full add-on support.")
        raise InstallError("ReShade too old")
    say()
    say(DISCLAIMER)
    say()
    if not ask("Install at your own risk?", args.yes, default=False):
        raise InstallError("cancelled")

    data.mkdir(parents=True, exist_ok=True)
    (data / "logs").mkdir(exist_ok=True)
    dxc = find_dxc(data, args.dxc, args.yes)
    say(f"DirectX Shader Compiler: {dxc} ({dxc_version(dxc)})")

    shaders = data / "shaders"
    if shaders.exists():
        shutil.rmtree(shaders)  # (stale patches from an older sim version)
    shaders.mkdir()
    work = data / "work"
    with open(data / "logs" / "install.log", "w", encoding="utf-8") as log:
        log.write(f"AtmosphereMod {version()} installer, {datetime.now():%Y-%m-%d %H:%M}\nsim {sim}\npak {pak}\n"
                  f"dxc {dxc}\n\n")
        patched, failed, pack = patch(pak, dxc, work, shaders, log)
    shutil.rmtree(work, ignore_errors=True)  # (the extracted game shaders aren't kept)

    # (a release may also carry replacement shaders of its own, for the variants this sim has)
    ours = sorted((payload / "shaders").glob("*.cso"))
    pack_set = set(pack)
    for f in ours:
        shutil.copy2(f, shaders / f.name)
    stale = [f.stem for f in ours if f.stem not in pack_set]
    say(f"Shaders: {patched} patched"
        + (f", {len(ours) - len(stale)} replaced" if ours else "")
        + (f"; {failed} not patched" if failed else "")
        + (f"; {len(stale)} replacements don't match this sim version" if stale else "")
        + (" (those parts stay stock; see logs\\install.log)" if failed or stale else ""))

    # settings: keep the user's
    defaults = (payload / "tunables.ini").read_text(encoding="utf-8")
    tunables = data / "tunables.ini"
    shutil.copy2(payload / "tunables.ini", data / "tunables.default.ini")
    if tunables.exists():
        merged, added = merge_tunables(defaults, tunables.read_text(encoding="utf-8"))
        tunables.write_text(merged, encoding="utf-8")
        say(f"Settings: kept your tunables.ini" + (f", added {', '.join(added)}" if added else ""))
    else:
        tunables.write_text(defaults, encoding="utf-8")
        say("Settings: tunables.ini installed")

    # the add-on and ReShade.ini
    shutil.copy2(payload / ADDON, sim / ADDON)
    ini = sim / "ReShade.ini"
    if ini.exists() and not (sim / BACKUP).exists():
        shutil.copy2(ini, sim / BACKUP)
    text = read_ini(ini)
    for key, value in (("DataPath", str(data)), ("TunablesPath", str(tunables)),
                       ("InjectRootSignature", "1"), ("InjectGraphicsRootSignature", "1")):
        text = set_key(text, key, value)
    ini.write_text(text, encoding="utf-8")
    say(f"Add-on: {sim / ADDON}; ReShade.ini [{SECTION}] set" + (f" (backup {BACKUP})" if (sim / BACKUP).exists()
                                                                    else ""))

    # the EFB app
    if community:
        target = community / EFB
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(payload / "efb" / EFB, target)
        say(f"EFB app: {target}")

    record = {"version": version(), "date": f"{datetime.now():%Y-%m-%d %H:%M}", "sim": str(sim),
              "community": str(community) if community else None, "data": str(data), "pak": pak.name,
              "patched": patched, "failed": failed}
    (data / "install.json").write_text(json.dumps(record, indent=2))
    say()
    say("Done. Start the sim; Ctrl+Shift+F9 switches between the mod and stock, Ctrl+Shift+F10 reloads")
    say(f"the settings in {tunables}. Run this installer again after every sim update.")


def uninstall(args) -> None:
    data = args.data
    record = json.loads((data / "install.json").read_text()) if (data / "install.json").exists() else {}
    if sim_running():
        raise InstallError("Microsoft Flight Simulator 2024 is running: close it first")
    sim = (sim_folder(args.sim) or args.sim) if args.sim else (Path(record["sim"]) if record.get("sim") else None)
    sims = [sim] if sim else find_sims()
    if not sims:
        manual = ask_sim_folder(args.yes)
        sims = [manual] if manual else []
    community = args.community or (Path(record["community"]) if record.get("community") else community_folder())
    if community is None and not record:
        community = ask_community_folder(args.yes, "the EFB app, if installed, stays")
    if not ask("Uninstall AtmosphereMod?", args.yes):
        raise InstallError("cancelled")
    for s in sims:
        if (s / ADDON).exists():
            (s / ADDON).unlink()
            say(f"removed {s / ADDON}")
        ini = s / "ReShade.ini"
        if ini.exists() and f"[{SECTION}]" in read_ini(ini):
            ini.write_text(remove_section(read_ini(ini)), encoding="utf-8")
            say(f"removed [{SECTION}] from {ini}")
    if community and (community / EFB).exists():
        shutil.rmtree(community / EFB)
        say(f"removed {community / EFB}")
    if data.exists() and ask(f"Also delete {data} (patched shaders, logs and your tunables.ini)?", args.yes):
        shutil.rmtree(data)
        say(f"removed {data}")
    say("Uninstalled.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sim", type=Path, help="the folder with FlightSimulator2024.exe (found automatically)")
    ap.add_argument("--pak", type=Path, help="the shader pack (default: the sim's Shaders folder)")
    ap.add_argument("--community", type=Path, help="the Community folder (default: from UserCfg.opt)")
    ap.add_argument("--data", type=Path, default=Path(os.environ.get("LOCALAPPDATA", ".")) / "AtmosphereMod",
                    help="where the patched shaders, settings and logs go")
    ap.add_argument("--dxc", type=Path, help="a folder with dxc.exe, dxcompiler.dll and dxil.dll")
    ap.add_argument("--payload", type=Path, help=argparse.SUPPRESS)
    ap.add_argument("--yes", action="store_true", help="don't ask")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()
    if sys.version_info < (3, 10):
        say("Python 3.10 or newer is needed.")
        return 1
    try:
        (uninstall if args.uninstall else install)(args)
    except InstallError as e:
        say(f"\nNot installed: {e}" if not args.uninstall else f"\nStopped: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

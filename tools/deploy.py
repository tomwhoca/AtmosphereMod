"""Install the add-on into the sim and point it at this project's build folder.

    python tools/deploy.py

Copies build/AtmosphereMod.addon64 next to ReShade in the sim's Content folder
and sets [ATMOSPHEREMOD] DataPath (build/) and TunablesPath
(shaders/tunables.ini) in ReShade.ini, so rebuilt shaders and edited tunables
are picked up with Ctrl+Shift+F10 without redeploying. Refuses to run
while the sim is open (the DLL would be locked and ReShade rewrites its ini on
exit). ReShade.ini is backed up before the first change.
"""

import argparse
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONTENT = Path(r"D:\XboxGames\Microsoft Flight Simulator 2024\Content")


def sim_running() -> bool:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FlightSimulator2024.exe", "/NH"],
                         capture_output=True, text=True).stdout
    return "FlightSimulator2024.exe" in out


def set_key(ini: Path, key: str, value: Path | str) -> None:
    text = ini.read_text(encoding="utf-8-sig")
    line = f"{key}={value}"
    section = re.search(r"^\[ATMOSPHEREMOD\][^\[]*", text, re.M)
    if section is None:
        text = text.rstrip("\n") + f"\n\n[ATMOSPHEREMOD]\n{line}\n"
    elif re.search(rf"^{key}=.*$", section.group(0), re.M):
        body = re.sub(rf"^{key}=.*$", line.replace("\\", "\\\\"), section.group(0), flags=re.M)
        text = text[:section.start()] + body + text[section.end():]
    else:
        body = section.group(0).rstrip("\n") + f"\n{line}\n\n"
        text = text[:section.start()] + body + text[section.end():]
    ini.write_text(text, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--content", type=Path, default=DEFAULT_CONTENT)
    args = ap.parse_args()

    if sim_running():
        raise SystemExit("Close MSFS first.")
    addon = ROOT / "build" / "AtmosphereMod.addon64"
    if not addon.exists():
        raise SystemExit("Run tools/build_addon.py first.")
    ini = args.content / "ReShade.ini"
    if not ini.exists():
        raise SystemExit(f"No ReShade.ini in {args.content}; is ReShade installed there?")

    shutil.copy2(addon, args.content / addon.name)
    backup = ini.with_name("ReShade.ini.atmospheremod.bak")
    if not backup.exists():
        shutil.copy2(ini, backup)
    set_key(ini, "DataPath", ROOT / "build")
    tunables = ROOT / "shaders" / "tunables.ini"  # (your own, from the shipping defaults on first use)
    if not tunables.exists():
        shutil.copy2(ROOT / "shaders" / "tunables.defaults.ini", tunables)
    set_key(ini, "TunablesPath", tunables)
    # Root constants for our tunables: compute passes, and the graphics root
    # signature for the patched ps_deferredRendering. Set either to 0 in
    # ReShade.ini to fall back to stock for those passes.
    set_key(ini, "InjectRootSignature", "1")
    set_key(ini, "InjectGraphicsRootSignature", "1")
    print(f"installed {addon.name} -> {args.content}")
    print(f"ReShade.ini DataPath -> {ROOT / 'build'}, TunablesPath -> shaders/tunables.ini, "
          f"InjectRootSignature=1, InjectGraphicsRootSignature=1 (backup: {backup.name})")

    # The EFB weather-bridge package (tools/build_efb.py), if built.
    efb = ROOT / "build" / "efb" / "atmospheremod-weather"
    if efb.exists():
        community = community_folder()
        target = community / efb.name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(efb, target)
        print(f"installed EFB package {efb.name} -> {community}")


def community_folder() -> Path:
    """<InstalledPackagesPath>\\Community, from MSFS 2024's UserCfg.opt (MS Store or Steam)."""
    import os
    for cfg in (Path(os.environ["LOCALAPPDATA"]) / "Packages/Microsoft.Limitless_8wekyb3d8bbwe/LocalCache/UserCfg.opt",
                Path(os.environ["APPDATA"]) / "Microsoft Flight Simulator 2024/UserCfg.opt"):
        if cfg.exists():
            m = re.search(r'^InstalledPackagesPath\s+"([^"]+)"', cfg.read_text(errors="ignore"), re.M)
            if m:
                return Path(m[1]) / "Community"
    raise SystemExit("MSFS 2024 UserCfg.opt with InstalledPackagesPath not found")


if __name__ == "__main__":
    main()

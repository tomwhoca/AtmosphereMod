"""Build a release folder and zip: dist/AtmosphereMod-<version>/ (only our own files).

    python tools/make_release.py [--version 0.1.0] [--build] [--no-python]

The version defaults to the add-on's (project VERSION in addon/CMakeLists.txt).
--build first runs build_addon, build_efb and build_shaders. Python's
embeddable package is bundled from build/python/ (download it from python.org
into that folder; only versions whose SHA-256 is pinned below are accepted),
and Microsoft's DirectX Shader Compiler from build/dxc/ (the release zip
install.py pins). The release:

    Install AtmosphereMod.cmd, Uninstall AtmosphereMod.cmd, README.md (its
    Screenshots section points to the GitHub page: the images aren't
    shipped), THIRD-PARTY-NOTICES.txt, LICENSE
    installer/  install.py and the patcher (patch_dxil.py, disasm.py,
                dxil_assemble.py, extract_pak.py, manifest.json), VERSION,
                python/: the embeddable Python (with --no-python the system's
                3.10+ is used), and dxc/: the compiler's x64 binaries with
                the package's licences and release notes, unchanged
    payload/    AtmosphereMod.addon64, shaders/<crc>.cso (our own HLSL
                replacements, from public sources only), tunables.ini (the
                shipping defaults, shaders/tunables.defaults.ini), efb/

It contains none of the game's files: the installer patches each user's own
shaders on their PC. The checks at the end enforce that.
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import install  # (the compiler release it pins)

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
REPO = "https://github.com/tomwhoca/AtmosphereMod"
# python.org's embeddable packages we bundle, with the SHA-256 python.org
# publishes on the release page (checked on every release build).
PYTHON_EMBED = {
    "python-3.14.9-embed-amd64.zip": "3c01d7cfc666f746a6f064f60fb2156ab0ec464d3d738ab796371a46a9bb24a2",
}
# What we ship from the compiler's release zip. Its ReleaseNotes.md says
# LICENSE-LLVM.txt applies to every file but d3d12shader.h (MIT, not shipped);
# from 1.8.2502 on, that includes dxil.dll.
DXC_SHIPPED = ("bin/x64/dxc.exe", "bin/x64/dxcompiler.dll", "bin/x64/dxil.dll",
               "LICENSE-LLVM.txt", "LICENSE-MIT.txt", "LICENSE-MS.txt", "ReleaseNotes.md")

CMD = r"""@echo off
setlocal
cd /d "%~dp0"
set "PY="
if exist "installer\python\python.exe" set "PY=installer\python\python.exe"
if not defined PY (
  py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo Python 3.10 or newer is needed: https://www.python.org/downloads/
  pause
  exit /b 1
)
%PY% "installer\install.py" {args}%*
echo.
pause
"""

NOTICES = """Third-party notices for AtmosphereMod

ReShade add-on API headers (compiled into AtmosphereMod.addon64)
https://github.com/crosire/reshade
{reshade}

Microsoft Flight Simulator 2024 SDK
- The add-on links the SDK's SimConnect static library.
- The EFB app bundles the SDK's EFB framework (@efb/efb-api, from the SDK's EFB
  template sample). Both are used under the Microsoft Flight Simulator SDK
  licence terms.
- @microsoft/msfs-sdk (MIT) is provided by the sim at run time, not bundled.

Python (bundled in installer/python, unchanged: python.org's Windows
embeddable package {python})
Python Software Foundation License; see installer/python/LICENSE.txt.

Microsoft DirectX Shader Compiler {dxc_version} (bundled in installer/dxc,
unchanged: the x64 dxc.exe, dxcompiler.dll and dxil.dll from Microsoft's
release {dxc_zip}, https://github.com/microsoft/DirectXShaderCompiler)
Per the package's release notes (installer/dxc/ReleaseNotes.md, "Licenses"),
LICENSE-LLVM.txt applies to these files:

{llvm}

Microsoft Flight Simulator 2024
- None of the game's files are included. The installer extracts and patches
  the shaders of the user's own installation, on the user's PC.
"""


def run(*cmd):
    subprocess.run([sys.executable, *cmd], cwd=ROOT, check=True)


def addon_version() -> str:
    m = re.search(r"project\(AtmosphereMod VERSION (\d+\.\d+\.\d+)", (ROOT / "addon" / "CMakeLists.txt").read_text())
    if not m:
        sys.exit("no project(AtmosphereMod VERSION x.y.z) in addon/CMakeLists.txt")
    return m[1]


def release_readme() -> str:
    """README.md with its Screenshots section replaced by a link (the images stay on GitHub)."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    start = text.index("## Screenshots\n")
    end = text.index("\n## ", start) + 1
    return (text[:start] + "## Screenshots\n\nComparison screenshots, with the mod off and on, are on the GitHub page:\n"
            f"{REPO}#screenshots\n\n" + text[end:])


def ignored(path: Path) -> bool:
    """git's view: a private (git-ignored) file."""
    return subprocess.run(["git", "check-ignore", "-q", str(path.relative_to(ROOT))], cwd=ROOT).returncode == 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--version", help="default: the add-on's, from addon/CMakeLists.txt")
    ap.add_argument("--build", action="store_true", help="build the add-on, EFB app and shaders first")
    ap.add_argument("--no-python", action="store_true", help="don't bundle Python (the system's is used)")
    args = ap.parse_args()
    args.version = args.version or addon_version()
    if args.build:
        run("tools/build_addon.py")
        run("tools/build_efb.py")
        run("tools/build_shaders.py")

    out = DIST / f"AtmosphereMod-{args.version}"
    if out.exists():
        shutil.rmtree(out)
    inst, payload = out / "installer", out / "payload"
    (payload / "shaders").mkdir(parents=True)
    inst.mkdir()

    # the installer and the patcher
    for name in ("install.py", "patch_dxil.py", "dxil_assemble.py", "disasm.py", "extract_pak.py"):
        shutil.copy2(ROOT / "tools" / name, inst / name)
    shutil.copy2(ROOT / "shaders" / "manifest.json", inst / "manifest.json")
    (inst / "VERSION").write_text(args.version + "\n")
    python = None
    if not args.no_python:
        zips = sorted(f for f in (BUILD / "python").glob("python-*-embed-amd64.zip") if f.name in PYTHON_EMBED)
        if not zips:
            sys.exit("no pinned Python embeddable package in build/python (see PYTHON_EMBED), or pass --no-python")
        python = zips[-1]
        if hashlib.sha256(python.read_bytes()).hexdigest() != PYTHON_EMBED[python.name]:
            sys.exit(f"{python.name}: SHA-256 doesn't match python.org's")
        with zipfile.ZipFile(python) as z:
            z.extractall(inst / "python")
    dxc_zip = BUILD / "dxc" / install.DXC_URL.rsplit("/", 1)[1]
    if not dxc_zip.exists():
        sys.exit(f"{dxc_zip} missing: download it from {install.DXC_URL}")
    if hashlib.sha256(dxc_zip.read_bytes()).hexdigest() != install.DXC_SHA256:
        sys.exit(f"{dxc_zip.name}: SHA-256 doesn't match install.py's DXC_SHA256")
    with zipfile.ZipFile(dxc_zip) as z:
        for name in DXC_SHIPPED:
            (inst / "dxc" / name).parent.mkdir(parents=True, exist_ok=True)
            (inst / "dxc" / name).write_bytes(z.read(name))

    # the payload: the add-on, our own shaders (public sources only), settings, the EFB app
    shutil.copy2(BUILD / "AtmosphereMod.addon64", payload / "AtmosphereMod.addon64")
    manifest = json.loads((ROOT / "shaders" / "manifest.json").read_text())
    ours, skipped = set(), []
    for shader in manifest["shaders"]:
        src = ROOT / "shaders" / "src" / shader["source"]
        if not src.exists() or ignored(src):
            skipped.append(shader["source"])
            continue
        for target in shader["targets"]:
            cso = BUILD / "shaders" / f"{target['crc32']}.cso"
            if not cso.exists():
                sys.exit(f"{cso} missing: run tools/build_shaders.py (or --build)")
            shutil.copy2(cso, payload / "shaders" / cso.name)
            ours.add(cso.stem)
    shutil.copy2(ROOT / "shaders" / "tunables.defaults.ini", payload / "tunables.ini")
    shutil.copytree(BUILD / "efb" / "atmospheremod-weather", payload / "efb" / "atmospheremod-weather")

    # the front: wrappers, README, notices, licence
    (out / "Install AtmosphereMod.cmd").write_text(CMD.replace("{args}", ""), newline="\r\n")
    (out / "Uninstall AtmosphereMod.cmd").write_text(CMD.replace("{args}", "--uninstall "), newline="\r\n")
    (out / "README.md").write_text(release_readme(), encoding="utf-8")
    reshade = (ROOT / "third_party" / "reshade" / "LICENSE.md").read_text(encoding="utf-8")
    notices = (NOTICES.replace("{reshade}", reshade).replace("{python}", python.stem if python else "(not bundled)")
               .replace("{dxc_version}", install.DXC_VERSION).replace("{dxc_zip}", dxc_zip.name)
               .replace("{llvm}", (inst / "dxc" / "LICENSE-LLVM.txt").read_text(encoding="utf-8")))
    (out / "THIRD-PARTY-NOTICES.txt").write_text(notices, encoding="utf-8")
    if (ROOT / "LICENSE").exists():
        shutil.copy2(ROOT / "LICENSE", out / "LICENSE")

    # checks: only our own files
    problems = []
    for f in out.rglob("*"):
        rel = f.relative_to(out)
        if f.suffix.lower() == ".cso" and f.stem not in ours:
            problems.append(f"{rel}: not one of our own shaders")
        if any(p in ("extracted", "notes", "captures", "logs") for p in rel.parts):
            problems.append(f"{rel}: private or game-derived folder")
        src = ROOT / "tools" / f.name
        if f.suffix == ".py" and "python" not in rel.parts and (not src.exists() or ignored(src)):
            problems.append(f"{rel}: not a public tool")
    if problems:
        shutil.rmtree(out)
        sys.exit("release refused:\n  " + "\n  ".join(problems))

    zip_path = DIST / f"AtmosphereMod-{args.version}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(out.rglob("*")):
            z.write(f, Path(out.name) / f.relative_to(out))
    files = sum(1 for f in out.rglob("*") if f.is_file())
    print(f"{out.relative_to(ROOT)}: {files} files, {len(ours)} replacement shaders (everything else is patched "
          "on the user's PC)"
          + (f" (left out, private: {', '.join(skipped)})" if skipped else "")
          + (f"; Python from {python.name}" if python else "; no bundled Python (the system's 3.10+ is used)"))
    print(f"{zip_path.relative_to(ROOT)}: {zip_path.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()

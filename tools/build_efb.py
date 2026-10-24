"""Build the EFB weather-bridge app and assemble its community package.

    python tools/build_efb.py

Builds efb/PackageSources/AtmoWeather with the project's portable Node.js
(tools/node), then assembles build/efb/atmospheremod-weather: the bundle under
html_ui/efb_ui/efb_apps/AtmoWeather, plus manifest.json and layout.json. The
package is assembled directly rather than with fspackagetool, which starts the
sim to build; a Copy-only package needs nothing compiled. tools/deploy.py
installs it into the Community folder.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE = ROOT / "tools" / "node" / "node.exe"
NPM_CLI = ROOT / "tools" / "node" / "node_modules" / "npm" / "bin" / "npm-cli.js"
APP = ROOT / "efb" / "PackageSources" / "AtmoWeather"
PACKAGE = ROOT / "build" / "efb" / "atmospheremod-weather"
APP_DIR = "html_ui/efb_ui/efb_apps/AtmoWeather"
VERSION = "0.1.0"

# Windows FILETIME epoch (1601-01-01) to Unix epoch, in 100 ns units.
FILETIME_UNIX_OFFSET = 116444736000000000


def npm(*args: str, cwd: Path) -> None:
    env = {**os.environ, "PATH": f"{NODE.parent};{os.environ['PATH']}"}
    subprocess.run([str(NODE), str(NPM_CLI), *args], cwd=cwd, check=True, env=env)


def main() -> None:
    if not NODE.exists():
        raise SystemExit("tools/node missing: unpack a Node.js LTS zip there (the Windows x64 zip from nodejs.org)")
    efb_api = ROOT / "third_party" / "msfs_efb_sample" / "PackageSources" / "efb_api"
    if not (efb_api / "node_modules").exists():
        npm("install", "--no-audit", "--no-fund", cwd=efb_api)
    if not (APP / "node_modules").exists():
        npm("install", "--no-audit", "--no-fund", cwd=APP)
    npm("run", "build", cwd=APP)

    if PACKAGE.exists():
        shutil.rmtree(PACKAGE)
    shutil.copytree(APP / "dist", PACKAGE / APP_DIR)
    content = []
    for f in sorted(p for p in PACKAGE.rglob("*") if p.is_file()):
        st = f.stat()
        content.append({"path": f.relative_to(PACKAGE).as_posix(), "size": st.st_size,
                        "date": int(st.st_mtime * 10_000_000) + FILETIME_UNIX_OFFSET})
    (PACKAGE / "layout.json").write_text(json.dumps({"content": content}, indent=2))
    manifest = {
        "dependencies": [],
        "content_type": "MISC",
        "title": "AtmosphereMod Weather Bridge",
        "manufacturer": "",
        "creator": "AtmosphereMod",
        "package_version": VERSION,
        "minimum_game_version": "1.0.0",
        "release_notes": {"neutral": {"LastUpdate": "", "OlderHistory": ""}},
        "total_package_size": str(sum(c["size"] for c in content)).zfill(20),
    }
    (PACKAGE / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"-> {PACKAGE} ({len(content)} files)")


if __name__ == "__main__":
    main()

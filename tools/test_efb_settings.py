"""Check the EFB app's settings without the sim: runs
efb/PackageSources/AtmoWeather/test/settings.test.js with the project's Node.js
(the friendly units against the shipping defaults, the model's messages).

    python tools/test_efb_settings.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import build_efb  # noqa: E402


def main():
    build_efb.write_defaults()
    test = build_efb.APP / "test" / "settings.test.js"
    sys.exit(subprocess.run([str(build_efb.NODE), str(test)], cwd=build_efb.APP).returncode)


if __name__ == "__main__":
    main()

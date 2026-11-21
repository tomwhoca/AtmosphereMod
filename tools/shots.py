"""Screenshots of what each setting does, for the EFB app's explanations (developers only).

    python tools/shots.py list                 # the shots, by scene
    python tools/shots.py baseline             # every setting at the sim's value, saved as the preset "Sim defaults"
    python tools/shots.py <shot>               # the baseline plus that shot's values, e.g. T0, C9, C10, base@inside
    python tools/shots.py restore "My preset"  # back to a preset of the user's
    python tools/shots.py get T3 T6 T10        # values now
    python tools/shots.py set T13=0 T3=12 -- "note"  # values on the current settings (not the baseline), logged

The baseline is the sim's look: each setting at the value that is the sim's own (its 0, "off" or "the sim's"), the
mod's systems off (T0, C0), and the settings that only matter with them on at the mod's defaults, so a shot that
switches a system on shows it as it ships. Each shot is the baseline plus a few values: the setting it shows and
what it needs (the haze sliders need the atmosphere, the cloud sliders the cloud lighting); its "before" is the shot
with the same needs and that setting at the baseline (`before` below), so the app can show the two side by side.

The values go to the add-on through the EFB app's developer hook (window.__atmoDev, ui/app.ts: only with the
developers' shell, tools/build_efb.py --dev), over the sim's debugger (tools/coherent.py). Each shot applied is
logged with its time in build/shots/applied.log, to match the screenshots' files to the shots.
"""
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coherent  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "build" / "shots" / "applied.log"
TUNABLES = ROOT / "shaders" / "tunables.ini"  # (tools/deploy.py points the add-on here)
# settings the add-on takes from tunables.ini only, not from the app: edited in the file, then a reload
FILE_ONLY = ("C37",)
BASELINE_PRESET = "Sim defaults"

# ---- the baseline: the sim's values (raw, as tunables.ini has them)
BASELINE = {
    # the mod's atmosphere off: the sim's own, exactly. The manual haze at the mod's defaults (shown once T0 is on)
    "T0": 0, "T13": 0, "T16": 1, "T11": 0, "T9": 0,
    "T3": 4, "T1": 1.5, "T4": 0.6, "T6": 0.05, "T10": 1.3,
    "T8": 0, "C22": 0,
    "T12": 0,  # (not in the app) far haze stretch: 0 = stock
    # the mod's cloud lighting off: the sim's own clouds, exactly; each of its sliders at the sim's value
    "C0": 0, "C2": 0, "C1": 0.9, "C9": 0, "C12": 0, "C3": 0, "C36": 0, "C5": 0, "C6": 0, "C8": 0, "C10": 4,
    # clouds: the sim's layers, density and shapes
    "C20": 0, "C21": 50, "C16": 0, "C33": 0, "C34": 0, "C23": 0, "C28": 0, "C32": 0, "C17": 0, "C19": 1.5,
    "C24": 0.6, "C25": 0.5, "C26": 0.4, "C27": 0.3,
    "C37": 0, "C30": 0,  # (not in the app) varied cloud tops and top detail: 0 = the sim's
}

# the slider units (src/specs.ts)
LOBE_DEG = 0.77 * 180 / 3.141592653589793
GLORY_DEG_UM = 24
SIM_DEEP_LIGHT = 0.033
GLARE_LO, GLARE_HI = 0.5, 0.85

SCENES = {
    "haze-low": "Over land, 1,000-1,500 m above the ground, the sun 15-25 deg high ahead of you; a clear or nearly "
                "clear sky (a weather preset, so the haze is the same in every shot).",
    "haze-high": "The same area from about 4,000 m, looking at the horizon with the sun to the side; a few clouds "
                 "far off (for the distant clouds' fade).",
    "sunset": "The sun on the horizon, the sky overhead in the picture (look up a little).",
    "clouds-sun": "Cumulus around the sun, the sun 15-25 deg high, looking toward it.",
    "clouds-side": "A cumulus field from the side, about at cloud-base height, the sun high and to the side.",
    "clouds-behind": "Just above scattered cumulus or a deck, the sun low behind you, looking down toward your "
                     "shadow (for the glory and your aircraft's shadow).",
    "inside": "Inside a cloud, paused there, the wings in view.",
    "cirrus": "High clouds (cirrus, thin sheets) in view, from below.",
    "live": "Live weather near a reporting station: haze, and stratocumulus or altocumulus for the cloud types; for "
            "thunderstorms, a station that reports them.",
}

HAZE = {"T0": 1, "T13": 0}
LIGHT = {"C0": 1}
SOLID = {"C0": 1, "C16": 0.5}
CLOUDS_AT_DEFAULTS = {"C0": 1, "C2": 0.5, "C1": 0.9, "C3": 0.4, "C5": 0.12, "C6": 0.5, "C9": 0.5, "C12": 0.4,
                      "C16": 0.5, "C17": 0.33, "C23": 1, "C28": 1, "C37": 2}

# id: (scene, values on top of the baseline, what it shows, the shot it compares with)
SHOTS: dict[str, tuple[str, dict, str, str]] = {}


def shot(sid: str, scene: str, values: dict, what: str, before: str) -> None:
    SHOTS[sid] = (scene, values, what, before)


for scene in SCENES:
    shot(f"base@{scene}", scene, {}, "the baseline: the sim's look", "")
# the atmosphere
for scene in ("haze-low", "haze-high", "sunset", "live"):
    shot(f"T0@{scene}", scene, HAZE, "the mod's atmosphere on (manual haze at its defaults)", f"base@{scene}")
shot("T3", "haze-low", {**HAZE, "T3": 12}, "haze near the ground x12 (default x4)", "T0@haze-low")
shot("T6", "haze-low", {**HAZE, "T6": 0.3}, "brown haze 0.30 (default 0.05)", "T0@haze-low")
shot("T10-white", "haze-low", {**HAZE, "T10": 0.3}, "haze colour 0.3, humid white (default 1.3)", "T0@haze-low")
shot("T10-smoke", "haze-low", {**HAZE, "T10": 2}, "haze colour 2.0, smoke (default 1.3)", "T0@haze-low")
shot("T9", "haze-low", {**HAZE, "T9": GLARE_HI}, "sun glare 100 % (baseline: the sim's, about 85 %)", "T0@haze-low")
shot("T1", "haze-high", {**HAZE, "T1": 3.0}, "haze layer top 3.0 km (default 1.5 km)", "T0@haze-high")
shot("T4", "haze-high", {**HAZE, "T4": 1.6}, "haze above the layer x1.6 (default x0.6)", "T0@haze-high")
shot("T11", "haze-high", {**HAZE, "T11": 1}, "distant clouds fade creamy", "T0@haze-high")
shot("T8", "sunset", {**HAZE, "T8": 1}, "real-world sky colours", "T0@sunset")
shot("T13", "live", {"T0": 1, "T13": 1}, "haze from the METARs", "T0@live")
shot("T16-200", "live", {"T0": 1, "T13": 1, "T16": 2}, "haze strength 200 %", "T13")
shot("T16-50", "live", {"T0": 1, "T13": 1, "T16": 0.5}, "haze strength 50 %", "T13")
shot("C22", "clouds-side", {"C22": 1}, "daylight white balance", "base@clouds-side")
# cloud lighting
for scene in ("clouds-sun", "clouds-side", "clouds-behind"):
    shot(f"C0@{scene}", scene, LIGHT, "the mod's cloud lighting on, each slider at the sim's value", f"base@{scene}")
shot("C0-defaults", "clouds-sun", CLOUDS_AT_DEFAULTS, "the mod's clouds as they ship", "base@clouds-sun")
shot("C2", "clouds-sun", {**LIGHT, "C2": 0.5}, "silver lining 100 %", "C0@clouds-sun")
shot("C1", "clouds-sun", {**LIGHT, "C2": 0.5, "C1": round(1 - 1.5 / LOBE_DEG, 4)}, "silver lining 1.5 deg (default 4.4)",
     "C2")
shot("C3", "clouds-sun", {**LIGHT, "C3": 1.0}, "bounced light at cloud edges 100 %", "C0@clouds-sun")
shot("C36", "clouds-side", {**LIGHT, "C36": 1.0}, "sun-facing cloud sides 100 %", "C0@clouds-side")
shot("C5", "clouds-side", {**LIGHT, "C5": round(6 * SIM_DEEP_LIGHT, 4)}, "light deep inside clouds x6",
     "C0@clouds-side")
shot("C9", "clouds-behind", {**LIGHT, "C9": 1.5}, "glory 1.5", "C0@clouds-behind")
shot("C10", "clouds-behind", {**LIGHT, "C9": 1.5, "C10": round(GLORY_DEG_UM / 3, 4)}, "glory rings 3 deg (default 6)",
     "C9")
shot("C12", "clouds-behind", {**LIGHT, "C12": 1.0}, "your aircraft's shadow 100 %", "C0@clouds-behind")
shot("C6", "clouds-behind", {**LIGHT, "C6": 1.0}, "powder effect 100 %", "C0@clouds-behind")
# clouds
shot("C16", "clouds-side", SOLID, "solid clouds 0.5 (the default)", "C0@clouds-side")
shot("C17", "clouds-side", {**SOLID, "C17": 0.33}, "cloud bases: real", "C16")
shot("C28", "clouds-side", {**SOLID, "C28": 1}, "smooth cloud bases", "C16")
shot("C8", "clouds-side", {**LIGHT, "C8": 2}, "crisper cloud edges 2", "C0@clouds-side")
shot("C16@cirrus", "cirrus", SOLID, "solid clouds 0.5, high clouds x1.5 (the defaults)", "base@cirrus")
shot("C19", "cirrus", {**SOLID, "C19": 3}, "high-cloud thickness x3", "C16@cirrus")
shot("C33", "inside", {"C33": 10}, "visibility inside clouds 10 m (the default)", "base@inside")
shot("C34", "inside", {"C33": 10, "C34": 0.5}, "structure inside clouds 50 %", "C33")
shot("C16@live", "live", SOLID, "solid clouds 0.5 (the default)", "base@live")
shot("C23", "live", {**SOLID, "C23": 1}, "cloud types", "C16@live")
shot("C20", "live", {"C20": 1}, "clouds follow the METARs", "base@live")
shot("C32", "live", {"C32": 1}, "thunderstorms", "base@live")


def dev(expression: str):
    """An expression on the app's developer hook, in the tablet's view."""
    return coherent.evaluate(coherent.TABLET, f"(function(){{ const d = window.__atmoDev; if (!d) return 'no hook';"
                                              f" return {expression}; }})()")


def state() -> dict:
    s = dev("d.state()")
    if s == "no hook":
        raise SystemExit("the app's developer hook isn't there: a developer's build (tools/build_efb.py --dev, "
                         "tools/deploy.py, a restart), the tablet's app started")
    return json.loads(s) or {}


def value(s: dict, key: str):
    values = s.get("C" if key[0] == "C" else "T") or []
    i = int(key[1:])
    return values[i] if i < len(values) else None


def file_value(key: str):
    m = re.search(rf"^{key}\s*=\s*([-+0-9.eE]+)", TUNABLES.read_text(encoding="utf-8"), re.M)
    return float(m[1]) if m else None


def set_file(key: str, v: float) -> None:
    """One setting's line in tunables.ini (its comment, if any, kept)."""
    text = TUNABLES.read_text(encoding="utf-8")
    new, n = re.subn(rf"^({key}\s*=\s*)[-+0-9.eE]+", lambda m: f"{m[1]}{v:g}", text, count=1, flags=re.M)
    if n != 1:
        raise SystemExit(f"{key} isn't in {TUNABLES}")
    TUNABLES.write_text(new, encoding="utf-8", newline="")


def apply(values: dict) -> list[str]:
    """Sets the values (those the app can't set: in tunables.ini, then a reload), then waits until the add-on's
    state has them; returns those it doesn't."""
    in_file = {k: v for k, v in values.items() if k in FILE_ONLY}
    rest = {k: v for k, v in values.items() if k not in FILE_ONLY}
    changed = [k for k, v in in_file.items() if file_value(k) is None or abs(file_value(k) - v) > 1e-6]
    if changed:
        for k in changed:
            set_file(k, in_file[k])
        dev("(d.reload(), 'ok')")
        time.sleep(2)
    dev(f"(d.set({json.dumps(rest)}), 'ok')")
    end = time.time() + 6
    while True:
        s = state()
        off = [k for k, v in rest.items() if value(s, k) is None or abs(value(s, k) - v) > 1e-3]
        if not off or time.time() > end:
            return off + [k for k, v in in_file.items() if abs((file_value(k) or 0) - v) > 1e-6]
        time.sleep(0.3)


def log(line: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat(timespec='seconds')}  {line}\n")


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = argv[0]
    if cmd == "list":
        for scene, text in SCENES.items():
            print(f"\n{scene}: {text}")
            for sid, (sc, _values, what, before) in SHOTS.items():
                if sc == scene:
                    print(f"  {sid:<18} {what}{f'  (against {before})' if before else ''}")
        return 0
    if cmd == "baseline":
        s = state()
        if s.get("presetSaved") is False and "--force" not in argv:
            raise SystemExit(f"the settings have unsaved changes (preset {s.get('preset')!r}): save or undo them "
                             "first, or --force")
        off = apply(BASELINE)
        dev(f"(d.saveAs({json.dumps(BASELINE_PRESET)}), 'ok')")
        time.sleep(1)
        s = state()
        log(f"baseline (preset {BASELINE_PRESET!r}; was {s.get('preset')!r})")
        print(f"baseline set and saved as {BASELINE_PRESET!r}"
              + (f"; NOT taken: {', '.join(off)}" if off else "") + f"; preset now {s.get('preset')!r}")
        return 1 if off else 0
    if cmd == "restore":
        name = argv[1] if len(argv) > 1 else ""
        if not name:
            raise SystemExit("restore: which preset? (python tools/shots.py restore \"Tom's settings\")")
        dev(f"(d.load({json.dumps(name)}), 'ok')")
        time.sleep(1.5)
        s = state()
        log(f"restore {name!r}")
        print(f"preset now {s.get('preset')!r}, {'saved' if s.get('presetSaved') else 'unsaved'}")
        return 0
    if cmd == "get":
        s = state()
        print(f"preset {s.get('preset')!r} ({'saved' if s.get('presetSaved') else 'unsaved'}): "
              + ", ".join(f"{k} = {value(s, k) if k not in FILE_ONLY else file_value(k)}" for k in argv[1:]))
        return 0
    if cmd == "set":
        rest = argv[1:]
        note = ""
        if "--" in rest:
            note = " ".join(rest[rest.index("--") + 1:])
            rest = rest[:rest.index("--")]
        values = {k: float(v) for k, v in (a.split("=", 1) for a in rest)}
        off = apply(values)
        log(f"set {' '.join(rest)}" + (f"  ({note})" if note else ""))
        print(f"set {values}" + (f"  ({note})" if note else "") + (f"; NOT taken: {', '.join(off)}" if off else ""))
        return 1 if off else 0
    if cmd not in SHOTS:
        raise SystemExit(f"no shot {cmd!r}: python tools/shots.py list")
    scene, values, what, before = SHOTS[cmd]
    off = apply({**BASELINE, **values})
    log(f"shot {cmd}  ({scene}: {what})")
    print(f"{cmd}: {what}\n  scene {scene}: {SCENES[scene]}\n  values: {values or 'the baseline'}"
          + (f"\n  NOT taken by the add-on: {', '.join(off)}" if off else ""))
    return 1 if off else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

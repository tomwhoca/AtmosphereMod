"""Run an A/B test of the mod's cloud shaders in the sim: the add-on alternates two sets frame by frame in the
view and times the cloud pass on each side (addon/src/ab_test.h). Developer installs only (ReShade.ini
[ATMOSPHEREMOD] Developer=1, which tools/deploy.py sets). Hold one view while it runs, best in the sim's full
pause, full screen, with the sim's window in front the whole time (frames with another window in front are left
out). A test takes some 10-40 s: the add-on beeps once as it starts measuring, twice (rising) when the result is
in, low if it failed. Each test waits for the sim's window to be in front (--no-wait: it doesn't), and its frames
are analysed again with the working tree's statistics (build/lab/ab_test_test.exe, from tools/test_ab.py).

    python tools/ab.py alu                 # the loaded shaders (build/shaders) against build/ab/alu
    python tools/ab.py apc --a old         # build/ab/old against build/ab/apc
    python tools/ab.py alu --frames 1200   # more frames (100..4000; default 600)
    python tools/ab.py alu --repeat 3      # three tests in a row

Sets come from tools/build_ab.py. The result goes to events.log too; every timed dispatch to
build/logs/captures/<id>_ab.tsv.
"""
import argparse
import ctypes
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AB = ROOT / "build" / "ab"
CAPTURES = ROOT / "build" / "logs" / "captures"
ANALYSE = ROOT / "build" / "lab" / "ab_test_test.exe"  # (tools/test_ab.py builds it)


def sim_running() -> bool:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq FlightSimulator2024.exe", "/NH"],
                         capture_output=True, text=True).stdout
    return "FlightSimulator2024.exe" in out


def sim_in_front() -> bool:
    """Whether the sim's window is the one in front."""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    hwnd = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not process:
        return False
    try:
        name = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(name))
        ok = kernel32.QueryFullProcessImageNameW(process, 0, name, ctypes.byref(size))
        return bool(ok) and name.value.lower().endswith("flightsimulator2024.exe")
    finally:
        kernel32.CloseHandle(process)


def wait_for_front(timeout: float) -> None:
    if sim_in_front():
        return
    print("waiting for the sim's window to be in front ...", flush=True)
    end = time.monotonic() + timeout
    while not sim_in_front():
        if time.monotonic() > end:
            raise SystemExit("the sim never came to the front")
        time.sleep(0.5)
    time.sleep(2.0)  # (settle after the switch)


def analysed(test_id: str) -> str | None:
    """The test's frames analysed again with tools/lab's build of addon/src/ab_test.cpp, the working tree's
    statistics (the add-on installed may be older)."""
    tsv = CAPTURES / f"{test_id}_ab.tsv"
    if not ANALYSE.exists() or not tsv.exists():
        return None
    out = subprocess.run([str(ANALYSE), "--tsv", str(tsv)], capture_output=True, text=True).stdout.strip()
    return out.replace("A/B A vs B", "analysed", 1) or None


def run_once(b: str, a: str | None, frames: int, timeout: float) -> str:
    result = AB / "result.txt"
    result.unlink(missing_ok=True)
    request = AB / "request.txt"
    tmp = request.with_suffix(".tmp")
    tmp.write_text("".join([f"b={b}\n", f"a={a}\n" if a else "", f"frames={frames}\n"]), encoding="utf-8")
    tmp.replace(request)  # (whole, so the add-on never reads half of it)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if result.exists():
            time.sleep(0.2)  # (written whole by then)
            lines = result.read_text(encoding="utf-8", errors="replace").splitlines()
            if len(lines) >= 2:
                again = analysed(lines[0])
                return f"A/B {lines[0]}: {lines[1]}" + ("\n  " + again if again else "")
        time.sleep(0.5)
    request.unlink(missing_ok=True)
    raise SystemExit("no result: is the sim running with this project's add-on and Developer=1 (tools/deploy.py)?")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("b", help="the set to compare: build/ab/<b>")
    ap.add_argument("--a", help="the other side: build/ab/<a> (default: the loaded shaders, build/shaders)")
    ap.add_argument("--frames", type=int, default=600)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--no-wait", action="store_true", help="start without waiting for the sim to be in front")
    args = ap.parse_args()
    for name in filter(None, (args.a, args.b)):
        if not any((AB / name).glob("*.cso")):
            raise SystemExit(f"no shaders in {AB / name} (tools/build_ab.py {name})")
    if not sim_running():
        raise SystemExit("The sim isn't running.")
    for _ in range(args.repeat):
        if not args.no_wait:
            wait_for_front(timeout=900)
        print(run_once(args.b, args.a, args.frames, timeout=60 + args.frames / 10), flush=True)


if __name__ == "__main__":
    main()

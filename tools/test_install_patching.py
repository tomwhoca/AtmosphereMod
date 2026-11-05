"""Check how the installer runs the patcher and finds PowerShell (tools/install.py, tools/patch_dxil.py):

- PowerShell is found by its full path with nothing useful on PATH (issue #20), signature checks work
  with it (also with a ' in the path), and with no PowerShell at all they say "can't check" without an
  exception. The release's own compiler is used then; an earlier download isn't.
- run_jobs returns every result in order, at below normal priority, inherited by child processes
  (dxc). Workers that die as they start, or partway, give a quick switch to one at a time: no restarting
  of workers without end, no waiting for ever (issue #18).
- worker_count, and the marker a frozen run leaves behind, so the next run takes the lightest load.

    python tools/test_install_patching.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import install  # noqa: E402
import patch_dxil  # noqa: E402

STARTS = os.environ.get("TEST_WORKER_STARTS")
if __name__ == "__mp_main__" and STARTS:  # a worker process: it counts itself, and it may refuse to start
    with open(STARTS, "a") as f:
        f.write("x")
    if os.environ.get("TEST_WORKERS_DIE"):
        raise ImportError("this worker can't start")

failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f": {detail}" if detail and not ok else ""))
    failures += not ok


def add_one(n: int) -> int:
    return n + 1


def die_in_a_worker_at_5(n: int) -> int:
    import multiprocessing
    if n == 5 and multiprocessing.parent_process() is not None:
        os._exit(3)
    return n + 1


def priorities(_: int) -> tuple:
    child = subprocess.run([sys.executable, "-I", "-c", "import ctypes; k = ctypes.WinDLL('kernel32'); k.GetCurrentProcess.restype = ctypes.c_void_p; "
                            "k.GetPriorityClass.argtypes = [ctypes.c_void_p]; print(k.GetPriorityClass(k.GetCurrentProcess()))"], capture_output=True, text=True).stdout.strip()
    return patch_dxil._process_priority(), int(child)


def test_powershell(temp: Path) -> None:
    signed = patch_dxil.find_dxc().parent
    copy = temp / "O'Brien's dxc"
    copy.mkdir()
    for name in install.DXC_FILES:
        shutil.copy2(signed / name, copy / name)
    unsigned = temp / "unsigned"
    unsigned.mkdir()
    for name in install.DXC_FILES:
        (unsigned / name).write_bytes(b"not a program")
    saved = {k: os.environ.get(k) for k in ("PATH", "SystemRoot")}
    try:
        os.environ["PATH"] = str(temp)  # as on the PC of issue #20: PowerShell's folder isn't on PATH
        check("PowerShell found by its full path, PATH of no use", bool(install.powershell()) and Path(install.powershell()).is_absolute() and Path(install.powershell()).exists())
        check("signed compiler: valid", install.signed_by_microsoft(signed) is True)
        check("a ' in the path: valid", install.signed_by_microsoft(copy) is True)
        check("unsigned files: not valid", install.signed_by_microsoft(unsigned) is False)
        os.environ["SystemRoot"] = str(temp / "no such windows")
        check("no PowerShell at all: none found", install.powershell() is None)
        check("no PowerShell at all: 'can't check', no exception", install.signed_by_microsoft(signed) is None)
    finally:
        for k, v in saved.items():
            os.environ[k] = v


def test_find_dxc_without_powershell(temp: Path) -> None:
    own, data = temp / "release", temp / "data"
    for folder in (own / "dxc" / "bin" / "x64", data / "dxc" / "bin" / "x64"):
        folder.mkdir(parents=True)
        for name in install.DXC_FILES:
            (folder / name).write_bytes(b"x")
    real = install.HERE, install.signed_by_microsoft, install.dxc_version, install.download_dxc, os.environ.get("ProgramFiles(x86)")
    os.environ["ProgramFiles(x86)"] = str(temp / "no kits")
    install.signed_by_microsoft = lambda folder: None
    install.dxc_version = lambda dxc: install.DXC_VERSION
    install.download_dxc = lambda d: "downloaded"
    try:
        install.HERE = own
        found = install.find_dxc(data, None, True)
        check("can't check: the release's own compiler is used", found == own / "dxc" / "bin" / "x64" / "dxc.exe", str(found))
        install.HERE = temp / "no release copy"
        found = install.find_dxc(data, None, True)
        check("can't check: an earlier download isn't, it's fetched again (checksum)", found == "downloaded", str(found))
    finally:
        install.HERE, install.signed_by_microsoft, install.dxc_version, install.download_dxc, program_files = real
        os.environ["ProgramFiles(x86)"] = program_files


def test_run_jobs(temp: Path) -> None:
    jobs = list(range(patch_dxil.PARALLEL_MIN_JOBS + 40))
    expected = [n + 1 for n in jobs]
    starts = temp / "starts.txt"
    os.environ["TEST_WORKER_STARTS"] = str(starts)

    def count() -> int:
        return starts.stat().st_size if starts.exists() else 0

    t0 = time.time()
    check("workers: every result, in order", list(patch_dxil.run_jobs(add_one, jobs, 3)) == expected)
    check("workers: no more processes than asked for", count() <= 3, str(count()))
    check("workers and what they start run below normal", set(patch_dxil.run_jobs(priorities, jobs, 2)) == {(patch_dxil.BELOW_NORMAL_PRIORITY_CLASS, patch_dxil.BELOW_NORMAL_PRIORITY_CLASS)})
    before = patch_dxil._process_priority()
    check("one at a time: every result, in order", list(patch_dxil.run_jobs(add_one, jobs, 1)) == expected)
    check("one at a time: below normal while running", set(patch_dxil.run_jobs(lambda n: patch_dxil._process_priority(), jobs, 1)) == {patch_dxil.BELOW_NORMAL_PRIORITY_CLASS})
    check("one at a time: priority back afterwards", patch_dxil._process_priority() == before)

    starts.unlink(missing_ok=True)
    check("a worker dies partway: every result still, in order", list(patch_dxil.run_jobs(die_in_a_worker_at_5, jobs, 2)) == expected)
    check("a worker dies partway: no restarting", count() <= 2, str(count()))

    starts.unlink(missing_ok=True)
    os.environ["TEST_WORKERS_DIE"] = "1"
    t0 = time.time()
    results = list(patch_dxil.run_jobs(add_one, jobs, 4))
    took = time.time() - t0
    del os.environ["TEST_WORKERS_DIE"]
    check("workers can't start: every result, one at a time", results == expected)
    check("workers can't start: noticed quickly", took < 20, f"{took:.1f} s")
    check("workers can't start: no restarting", count() <= 4, f"{count()} started")
    del os.environ["TEST_WORKER_STARTS"]


def test_workers_and_marker(temp: Path) -> None:
    real = os.cpu_count
    try:
        for cores, expected in ((24, 4), (16, 4), (8, 4), (6, 3), (4, 2), (2, 1), (1, 1)):
            os.cpu_count = lambda n=cores: n
            check(f"{cores} logical cores: {expected} worker(s)", install.worker_count(None, False) == expected)
        os.cpu_count = lambda: None
        check("cores unknown: 1 worker", install.worker_count(None, False) == 1)
        os.cpu_count = lambda: 24
        check("after an unfinished run: 1 worker", install.worker_count(None, True) == 1)
        check("--workers wins, also after an unfinished run", install.worker_count(6, True) == 6)
        check("--workers 0 counts as 1", install.worker_count(0, False) == 1)
    finally:
        os.cpu_count = real
    marker = temp / install.UNFINISHED_MARKER
    with install.unfinished_marker(marker):
        check("marker there while patching", marker.exists())
    check("marker gone after a normal finish", not marker.exists())
    try:
        with install.unfinished_marker(marker):
            raise install.InstallError("reported as usual")
    except install.InstallError:
        pass
    check("marker gone after an error", not marker.exists())
    # a freeze or a power cut: the process just stops
    code = (f"import os, sys; sys.path.insert(0, {str(ROOT / 'tools')!r}); import install; from pathlib import Path\n"
            f"with install.unfinished_marker(Path({str(marker)!r})):\n    os._exit(9)\n")
    subprocess.run([sys.executable, "-c", code], check=False)
    check("a run that stops dead leaves the marker", marker.exists())
    marker.unlink(missing_ok=True)


def main() -> None:
    with tempfile.TemporaryDirectory() as folder:
        temp = Path(folder)
        for test, name in ((test_powershell, "ps"), (test_find_dxc_without_powershell, "dxc"), (test_run_jobs, "jobs"), (test_workers_and_marker, "marker")):
            (temp / name).mkdir()
            test(temp / name)
    print("All good." if not failures else f"{failures} FAILED")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()

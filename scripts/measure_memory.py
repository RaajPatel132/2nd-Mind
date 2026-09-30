#!/usr/bin/env python3
"""Peak memory of the rehearsal stack while a command runs (S4.5).

    scripts/measure_memory.py [--project secondmind-prodlike] -- <command> [args...]

Samples `docker stats` about once a second for every container of the compose project while the
command runs, then prints each container's peak against its limit, and the peak of the sum: what
the host would have needed. The limits in compose.prodlike.yaml add up to about 1.6 GB, and the
production host has 2 GB plus a swap file that is a cushion, not working memory.
Exits with the command's status.
"""

import argparse
import re
import subprocess
import sys
import threading
import time

UNITS = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "kB": 1000, "MB": 1000**2, "GB": 1000**3}


def to_bytes(text: str) -> float:
    match = re.match(r"([\d.]+)\s*([A-Za-z]+)", text.strip())
    return float(match.group(1)) * UNITS[match.group(2)] if match else 0.0


def sample(project: str) -> dict[str, tuple[float, float]]:
    out = subprocess.run(  # noqa: S603
        ["docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.MemUsage}}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    found: dict[str, tuple[float, float]] = {}
    for line in out.splitlines():
        name, _, usage = line.partition("|")
        if not name.startswith(project + "-"):
            continue
        used, _, limit = usage.partition("/")
        found[name.removeprefix(project + "-")] = (to_bytes(used), to_bytes(limit))
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="secondmind-prodlike")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = [c for c in args.command if c != "--"] or ["sleep", "1"]

    peaks: dict[str, float] = {}
    limits: dict[str, float] = {}
    peak_total = 0.0
    stop = threading.Event()

    def watch() -> None:
        nonlocal peak_total
        while not stop.is_set():
            now = sample(args.project)
            for name, (used, limit) in now.items():
                peaks[name] = max(peaks.get(name, 0.0), used)
                limits[name] = limit
            peak_total = max(peak_total, sum(used for used, _ in now.values()))
            time.sleep(0.5)

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    status = subprocess.run(command, check=False).returncode  # noqa: S603
    stop.set()
    thread.join(timeout=15)

    mib = 1024**2
    print("\nPeak memory while the command ran (MiB):")
    for name in sorted(peaks, key=lambda n: -peaks[n]):
        print(f"  {name:<12} {peaks[name] / mib:7.1f} of {limits[name] / mib:7.1f}")
    print(f"  {'sum of peaks':<12} {sum(peaks.values()) / mib:7.1f}")
    print(f"  {'peak at once':<12} {peak_total / mib:7.1f}   (2048 MiB host, minus about 250 for the OS)")
    return status


if __name__ == "__main__":
    sys.exit(main())

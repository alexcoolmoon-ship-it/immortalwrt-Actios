#!/usr/bin/env python3
"""Stop before firmware compilation if any selected source download fails.

The locked OpenWrt include/toplevel.mk expands `make download` to several
submakes separated by semicolons: a later success can hide an earlier failure.
Run those stages individually and preserve every exit code. OpenWrt's own
download machinery checks recipe hashes, including restored dl cache entries.
Do not reject ordinary mirror/HTTP errors: a verified fallback may succeed.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

STAGES = (
    ("download-tools", ("tools/flock/compile", "tools/zstd/compile")),
    ("tools", ("tools/download",)),
    ("toolchain", ("toolchain/download",)),
    ("packages", ("package/download",)),
    ("target", ("target/download",)),
)
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
TERMINAL_FAILURES = (
    re.compile(r"^\s*(?:g?make)(?:\[\d+\])?:\s+\*\*\*"),
    re.compile(r"^\s*ERROR:\s+.+ failed to build(?:\s|\.|$)"),
)


def terminal_failure(line):
    line = ANSI.sub("", line).strip()
    return any(pattern.search(line) for pattern in TERMINAL_FAILURES)


def download(root, jobs):
    root = Path(root).resolve()
    if not (root / ".config").is_file() or not (root / "include/toplevel.mk").is_file():
        raise ValueError("Expected the prepared OpenWrt tree with .config")
    report = {"schema": 1, "ok": False, "stages": []}
    report_path = root / "download-check.json"
    # Replace a previous success report immediately, including on exceptions.
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    env = dict(os.environ, LC_ALL="C")
    with (root / "download.log").open("w", encoding="utf-8") as log:
        for name, targets in STAGES:
            command = ["make", f"-j{jobs}", *targets, "IGNORE_ERRORS=", "V=s"]
            heading = "\n=== " + name + ": " + " ".join(command) + " ===\n"
            print(heading, end="", flush=True)
            log.write(heading)
            log.flush()
            errors = []
            with subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True,
                                  encoding="utf-8", errors="replace") as process:
                for line in process.stdout:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    log.write(line)
                    log.flush()
                    if terminal_failure(line) and len(errors) < 20:
                        errors.append(ANSI.sub("", line).rstrip())
                rc = process.wait()
            stage = {"name": name, "command": command, "returncode": rc,
                     "terminal_errors": errors, "ok": rc == 0 and not errors}
            report["stages"].append(stage)
            report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            if not stage["ok"]:
                print(f"DOWNLOAD CHECK FAILED at {name}; compilation must not start.",
                      file=sys.stderr)
                return 1
    report["ok"] = True
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("All selected source-download stages passed.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("openwrt", type=Path)
    parser.add_argument("--jobs", type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.jobs <= 64:
        parser.error("--jobs must be between 1 and 64")
    try:
        return download(args.openwrt, args.jobs)
    except (OSError, ValueError) as exc:
        print(f"DOWNLOAD CHECK FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

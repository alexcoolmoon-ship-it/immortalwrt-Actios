#!/usr/bin/env python3
"""Prepare a pinned OpenWrt 25.12.5 tree with the UFI001C port."""
import argparse
import json
import secrets
import shutil
import subprocess
from pathlib import Path

here = Path(__file__).resolve().parent
args = argparse.ArgumentParser()
args.add_argument("directory", type=Path)
opts = args.parse_args()
root = opts.directory.resolve()
lock = json.loads((here / "sources.lock.json").read_text())

def run(*cmd):
    return subprocess.run(cmd, cwd=root, check=True)

if not root.exists():
    root.mkdir(parents=True)
if not (root / ".git").exists():
    if any(root.iterdir()):
        raise SystemExit("Build directory must be empty or the matching OpenWrt checkout")
    run("git", "init", "-q")
    run("git", "remote", "add", "origin", "https://github.com/openwrt/openwrt.git")
    run("git", "fetch", "--depth=1", "origin", "refs/tags/v25.12.5")
    run("git", "checkout", "--detach", "FETCH_HEAD")
head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
if head != lock["openwrt_commit"]:
    raise SystemExit(f"Unexpected OpenWrt revision: {head}")
patch = str(here / "board-drivers.patch")
check = subprocess.run(["git", "apply", "--check", patch], cwd=root, capture_output=True)
if check.returncode == 0:
    run("git", "apply", patch)
else:
    run("git", "apply", "--reverse", "--check", patch)
shutil.copytree(here / "overlay", root, dirs_exist_ok=True)
shutil.copy2(here / "feeds.conf", root / "feeds.conf")
shutil.copy2(root / "ufi001c.config", root / ".config")
# Force package metadata to include the newly added driver definitions.
for file in ("package/kernel/linux/Makefile", "package/kernel/mac80211/Makefile"):
    (root / file).touch()
keyfile = root / "files/etc/ufi001c-wifi-key"
keyfile.parent.mkdir(parents=True, exist_ok=True)
if not keyfile.exists():
    keyfile.write_text(secrets.token_hex(10) + "\n")
keyfile.chmod(0o600)
(root / "ACCESS.txt").write_text(
    "Wi-Fi: OpenWrt-25-UFI001C\n"
    f"Wi-Fi password: {keyfile.read_text().strip()}\n"
    "LuCI: http://192.168.1.1/\n"
    "Username: root\n"
    "Initial administrator password: empty; set it after signing in.\n"
    "The mobile interface is initially disabled; enter your carrier APN and enable it.\n"
)
print(f"Prepared OpenWrt {lock['openwrt_version']} at {root}")

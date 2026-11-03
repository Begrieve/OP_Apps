"""Build a self-contained Broker Desk package (a single ZIP) for a laptop or office network.

    python tools/make_package.py             # code only; the first start downloads what it needs
    python tools/make_package.py --offline   # also bundle the install files for Windows and Mac,
                                              # so even the first start works without internet

The ZIP holds everything needed to run Broker Desk without GitHub or PythonAnywhere. It never
contains a database, backups or passwords: data is restored inside the app from a backup file.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", ".venv", "data", "dist", "wheels", "__pycache__", ".pytest_cache", "node_modules"}
SKIP_FILES = {".tracker_secret", "insurance.db"}
SKIP_EXT = {".db", ".pyc", ".sqlite", ".sqlite3", ".log"}
EXECUTABLE = {"Start Broker Desk.command", "start-broker-desk.sh", "deploy/update.sh"}
PLATFORMS = {  # pip platform tags to bundle with --offline
    "Windows": ["win_amd64"],
    "Mac (Apple chip)": ["macosx_11_0_arm64", "macosx_10_9_universal2"],
    "Mac (Intel)": ["macosx_10_9_x86_64", "macosx_10_9_universal2"],
}
PY_VERSIONS = ["3.10", "3.11", "3.12", "3.13"]
# Requirements whose environment marker says "Windows only" are skipped by pip when it
# downloads from another system, so name them explicitly.
EXTRA = {"Windows": ["colorama", "tzdata"], "Mac (Apple chip)": [], "Mac (Intel)": []}


def files_to_package():
    for folder, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if name in SKIP_FILES or os.path.splitext(name)[1].lower() in SKIP_EXT:
                continue
            full = os.path.join(folder, name)
            yield full, os.path.relpath(full, ROOT).replace(os.sep, "/")


def download_wheels(target):
    req = os.path.join(ROOT, "requirements.txt")
    for label, tags in PLATFORMS.items():
        for py in PY_VERSIONS:
            cmd = [sys.executable, "-m", "pip", "download", "-q", "--disable-pip-version-check",
                   "--only-binary=:all:", "--python-version", py, "-d", target, "-r", req]
            for t in tags:
                cmd += ["--platform", t]
            cmd += EXTRA[label]
            print(f"  downloading install files for {label}, Python {py}...")
            if subprocess.run(cmd, stderr=subprocess.DEVNULL).returncode != 0:
                print(f"    (not available for {label} with Python {py}; that combination will "
                      f"download what it needs on first start instead)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="bundle install files for Windows and Mac")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"), help="output folder")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    name = f"BrokerDesk-{date.today().isoformat()}{'-offline' if args.offline else ''}.zip"
    out = os.path.join(args.out, name)
    top = "BrokerDesk"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        count = 0
        for full, rel in files_to_package():
            info = zipfile.ZipInfo.from_file(full, f"{top}/{rel}")
            mode = 0o755 if rel in EXECUTABLE else 0o644
            info.external_attr = (0o100000 | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(full, "rb") as fh:
                z.writestr(info, fh.read())
            count += 1
        if args.offline:
            tmp = tempfile.mkdtemp()
            try:
                download_wheels(tmp)
                for w in sorted(os.listdir(tmp)):
                    z.write(os.path.join(tmp, w), f"{top}/wheels/{w}")
                    count += 1
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
    print(f"\nBuilt {out}  ({count} files, {os.path.getsize(out) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()

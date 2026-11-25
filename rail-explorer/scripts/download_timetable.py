#!/usr/bin/env python3
"""
Download the full GB rail timetable feed (RSP CIF, RJTTF*) and unzip it into
data/cif/.

Rail Data Marketplace's "Timetable - Full Refresh - Weekly" product
(P-4b4a43c6-3507-4c6f-aeed-82d853264236) has no download API; instead RDM
copies each new release into our own S3 bucket as timetable_full.zip, which
this script fetches with the AWS CLI. Credentials come from the usual AWS
CLI chain -- a local `aws configure` profile, or the OIDC role the GitHub
Actions data-refresh workflow assumes.

Bucket and key can be overridden with the RDM_BUCKET/RDM_TIMETABLE_KEY
environment variables.

Output: data/cif/timetable.zip, extracted into data/cif/
"""

import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
CIF_DIR = ROOT / "data" / "cif"
ZIP_FILE = CIF_DIR / "timetable.zip"

BUCKET = os.environ.get("RDM_BUCKET", "rdm-feeds")
KEY = os.environ.get("RDM_TIMETABLE_KEY", "timetable_full.zip")


def main():
    CIF_DIR.mkdir(parents=True, exist_ok=True)
    # Clear out the previous extract so build_timetable_db.py can't pick up
    # a stale .MCA from an older release (file numbers change per release)
    for old in CIF_DIR.iterdir():
        if old.is_file():
            old.unlink()

    src = f"s3://{BUCKET}/{KEY}"
    print(f"Downloading {src} (this may take a while)...")
    result = subprocess.run(["aws", "s3", "cp", "--only-show-errors", src, str(ZIP_FILE)])
    if result.returncode != 0:
        sys.exit(f"Download of {src} failed (exit {result.returncode})")
    print(f"Saved to {ZIP_FILE} ({ZIP_FILE.stat().st_size / 1e6:.1f} MB)")

    print("Extracting...")
    with zipfile.ZipFile(ZIP_FILE) as zf:
        names = zf.namelist()
        zf.extractall(CIF_DIR)
    print(f"Extracted {len(names)} file(s) to {CIF_DIR}:")
    for n in names:
        print(f"  {n}")


if __name__ == "__main__":
    main()

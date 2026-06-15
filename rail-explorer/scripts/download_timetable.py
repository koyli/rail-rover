#!/usr/bin/env python3
"""
Download the full GB rail timetable feed from the National Rail Data Portal
(opendata.nationalrail.co.uk) and unzip it into data/cif/.

Requires credentials, either via config.json (see config.example.json) with
nrdp_username/nrdp_password, or via the NRDP_USERNAME/NRDP_PASSWORD
environment variables (used by the GitHub Actions data-refresh workflow).

Output: data/cif/timetable.zip, extracted into data/cif/
"""

import json
import os
import sys
import urllib.request
import urllib.parse
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
CONFIG_FILE = ROOT / "config.json"
CIF_DIR = ROOT / "data" / "cif"
ZIP_FILE = CIF_DIR / "timetable.zip"

AUTHENTICATE_URL = "https://opendata.nationalrail.co.uk/authenticate"
TIMETABLE_URL = "https://opendata.nationalrail.co.uk/api/staticfeeds/3.0/timetable"


def main():
    if CONFIG_FILE.exists():
        config = json.load(open(CONFIG_FILE))
        username = config["nrdp_username"]
        password = config["nrdp_password"]
    else:
        username = os.environ.get("NRDP_USERNAME")
        password = os.environ.get("NRDP_PASSWORD")
        if not username or not password:
            sys.exit(f"No {CONFIG_FILE} and no NRDP_USERNAME/NRDP_PASSWORD env vars set")

    print(f"Authenticating as {username}...")
    data = urllib.parse.urlencode({"username": username, "password": password}).encode()
    req = urllib.request.Request(AUTHENTICATE_URL, data=data, method="POST")
    with urllib.request.urlopen(req) as resp:
        auth = json.load(resp)
    token = auth.get("token")
    if not token:
        sys.exit(f"Authentication failed: {auth}")

    print("Authenticated. Downloading timetable feed (this may take a while)...")
    CIF_DIR.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(TIMETABLE_URL, headers={"X-Auth-Token": token})
    with urllib.request.urlopen(req) as resp, open(ZIP_FILE, "wb") as f:
        total = 0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            total += len(chunk)
            print(f"\r  {total / 1e6:.1f} MB", end="", flush=True)
    print(f"\nSaved to {ZIP_FILE} ({total / 1e6:.1f} MB)")

    print("Extracting...")
    with zipfile.ZipFile(ZIP_FILE) as zf:
        names = zf.namelist()
        zf.extractall(CIF_DIR)
    print(f"Extracted {len(names)} file(s) to {CIF_DIR}:")
    for n in names:
        print(f"  {n}")


if __name__ == "__main__":
    main()

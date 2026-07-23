"""Explore the DWD Climate Data Center archive.

Phase 1, step 1. This is a probe, not pipeline code. It downloads the smallest
station archive it can find and prints the real structure so we can write the
parser against facts instead of assumptions.

Run:  python scripts/explore_dwd.py
"""

import io
import re
import zipfile

import httpx

BASE = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate"
RESOLUTION = "hourly"
PARAMETER = "air_temperature"
MODE = "recent"

UA = {"User-Agent": "mcp-agent-portfolio/1.0 (github.com/suhasnu)"}
DIR_URL = f"{BASE}/{RESOLUTION}/{PARAMETER}/{MODE}/"


def get(url: str) -> httpx.Response:
    resp = httpx.get(url, headers=UA, timeout=120, follow_redirects=True)
    resp.raise_for_status()
    return resp


def list_links(html: str) -> list[str]:
    """Pull filenames out of the Apache directory index."""
    return re.findall(r'href="([^"?/][^"]*)"', html)


def show(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def main() -> None:
    show(f"1. Directory listing: {DIR_URL}")
    links = list_links(get(DIR_URL).text)
    zips = [n for n in links if n.endswith(".zip")]
    others = [n for n in links if not n.endswith(".zip")]

    print(f"Found {len(zips)} station archives.")
    print("First 5 archive names:")
    for name in zips[:5]:
        print(f"  {name}")
    print("\nNon-zip files in this directory:")
    for name in others:
        print(f"  {name}")

    # --- station metadata file ---
    meta_name = next((n for n in others if "Beschreibung_Stationen" in n), None)
    if meta_name:
        show(f"2. Station metadata: {meta_name}")
        text = get(DIR_URL + meta_name).content.decode("latin-1")
        lines = text.splitlines()
        print(f"{len(lines)} lines. First 6, with a ruler so we can find column positions:\n")
        print("".join(str(i % 10) for i in range(120)))
        for line in lines[:6]:
            print(line[:120])

    # --- one station archive ---
    if not zips:
        print("\nNo archives found, stopping.")
        return

    target = zips[0]
    show(f"3. Downloading one archive: {target}")
    blob = get(DIR_URL + target).content
    print(f"Downloaded {len(blob) / 1024:.1f} KB")

    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        print("\nMembers:")
        for info in zf.infolist():
            print(f"  {info.filename}  ({info.file_size / 1024:.1f} KB)")

        data_name = next((n for n in zf.namelist() if n.startswith("produkt")), None)
        if not data_name:
            print("\nNo produkt_* member found. Member names above are the truth.")
            return

        show(f"4. Data file: {data_name}")
        rows = zf.read(data_name).decode("latin-1").splitlines()
        print(f"{len(rows)} rows total.\n")
        print("Header:")
        print(f"  {rows[0]}")
        print("\nFirst 3 data rows:")
        for row in rows[1:4]:
            print(f"  {row}")
        print("\nLast row:")
        print(f"  {rows[-1]}")

        show("5. Column analysis")
        cols = [c.strip() for c in rows[0].split(";")]
        vals = [v.strip() for v in rows[1].split(";")]
        print(f"{len(cols)} columns:\n")
        for col, val in zip(cols, vals):
            print(f"  {col:<12} example: {val}")

        sentinels = [c for c, v in zip(cols, vals) if v.lstrip("-").isdigit() and v.startswith("-999")]
        if sentinels:
            print(f"\nSentinel (-999) values present in: {sentinels}")


if __name__ == "__main__":
    main()
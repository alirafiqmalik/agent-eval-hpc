"""Fetch the upstream public task ZIPs, with resumable downloads and CRC checks.

The saved Drive index was resolved by upstream's 00_download_tasks_from_drive.py
from folder 13OsxAM7t5xTnuqRoyVdMIApNuCJA4SyP on 2026-10-13. curl follows the
public download redirect without depending on gdown's HTML confirmation parser.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode


class Confirmation(HTMLParser):
    """Read Google's public download confirmation form for unscanned archives."""

    def __init__(self):
        super().__init__()
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("type") == "hidden":
            if attrs.get("name") in ("id", "export", "confirm", "uuid"):
                self.fields[attrs["name"]] = attrs.get("value", "")


def valid(path: Path) -> bool:
    if not path.is_file() or not zipfile.is_zipfile(path):
        return False
    with zipfile.ZipFile(path) as archive:
        return archive.testzip() is None


def download(directory: Path, filename: str, file_id: str) -> str:
    target = directory / filename
    if valid(target):
        return filename
    temporary = target.with_suffix(".zip.part")
    command = [
        "curl",
        "--fail",
        "--location",
        "--silent",
        "--show-error",
        "--retry",
        "3",
        "--retry-delay",
        "5",
        "--max-time",
        "180",
        "--output",
        str(temporary),
    ]
    subprocess.run(
        command + [f"https://drive.google.com/uc?id={file_id}&export=download"], check=True
    )
    if not valid(temporary) and temporary.stat().st_size < 100_000:
        confirmation = Confirmation()
        confirmation.feed(temporary.read_text(errors="replace"))
        if confirmation.fields.get("id") == file_id and confirmation.fields.get("confirm"):
            url = "https://drive.usercontent.google.com/download?" + urlencode(confirmation.fields)
            subprocess.run(command + [url], check=True)
    if not valid(temporary):
        raise RuntimeError(f"Drive did not return a valid archive: {filename}")
    temporary.replace(target)
    return filename


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip_dir", type=Path)
    args = parser.parse_args()
    args.zip_dir.mkdir(parents=True, exist_ok=True)
    index = json.loads(Path(__file__).with_name("drive-index.json").read_text())
    failures = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = {
            pool.submit(download, args.zip_dir, name, id): name for name, id in index.items()
        }
        for number, future in enumerate(as_completed(pending), 1):
            try:
                print(f"{number}/{len(index)} {future.result()}", flush=True)
            except Exception as error:
                failures.append(pending[future])
                print(f"Failed {pending[future]}: {error}", flush=True)
    if failures:
        raise SystemExit(f"Incomplete download ({len(failures)} failures); rerun to resume")


if __name__ == "__main__":
    main()

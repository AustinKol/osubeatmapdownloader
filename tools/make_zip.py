"""Zip the built app folder for a release, leaving out anything created by running it."""
import sys
import zipfile
from pathlib import Path

app_dir, zip_path = Path(sys.argv[1]), Path(sys.argv[2])
SKIP = {"data", "downloads"}  # settings, saved sign-in and beatmaps from test runs

with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
    for path in sorted(app_dir.rglob("*")):
        rel = path.relative_to(app_dir)
        if rel.parts[0] in SKIP or path.is_dir():
            continue
        zf.write(path, Path(app_dir.name) / rel)
print(f"Wrote {zip_path}")

import os
import subprocess
import time
from datetime import datetime

# Songs folder relative to the script location
folder_path = os.path.join(os.path.dirname(__file__), "songs")

# Step 1: collect files + their creation timestamps
entries = []
with os.scandir(folder_path) as it:
    for entry in it:
        if entry.is_file() and entry.name.endswith(".osz"):
            ctime = entry.stat().st_ctime  # creation timestamp
            entries.append((ctime, entry.path))

# Step 2: sort by creation time (oldest → newest)
entries.sort(key=lambda x: x[0])

# Step 3: open files one by one, waiting 0.5 seconds between each
for ctime, full_path in entries:
    timestamp = datetime.fromtimestamp(ctime).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] Opening: {full_path}")

    subprocess.Popen(['start', '', full_path], shell=True)

    # wait 0.5 seconds before opening next map
    time.sleep(0.5)

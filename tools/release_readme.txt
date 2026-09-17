osu! Beatmap Downloader
=======================

1. Make sure Google Chrome is installed.
2. Double-click "osu! Beatmap Downloader.exe".
   Windows may say it protected your PC - click "More info", then "Run anyway".
3. A browser tab opens with the app. Keep the black window open while downloading;
   close it to quit.

All beatmaps are downloaded directly from osu.ppy.sh (the official osu!
website) using your own account. No third-party mirrors are used.

Heads-up: osu! allows about 200 beatmap downloads per hour (more for
osu!supporters). When you reach it, the app waits and retries by itself after
5, 10, 20 and 25 minutes, then keeps going. Big batches take roughly an hour
per 200 maps - just leave it running.

Everything the app creates stays in this folder:

  downloads\   beatmaps (.osz) waiting to be imported into osu!
  data\        your settings, saved osu! sign-in and download history
  runtime\     the app's own files - don't touch

To move the app, move the whole folder. To uninstall, delete the folder.
Keep the folder private: data\ contains your saved osu! sign-in.
Use "Sign out" in the app to remove it.

Don't unzip into Program Files - the app needs to be able to write to its own
folder (if it can't, it falls back to %LOCALAPPDATA%\osu! Beatmap Downloader).

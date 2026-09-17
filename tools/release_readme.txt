osu! Beatmap Downloader
=======================

1. Make sure Google Chrome is installed.
2. Double-click "osu! Beatmap Downloader.exe".
   Windows may say it protected your PC - click "More info", then "Run anyway".
3. A browser tab opens with the app. Keep the black window open while downloading;
   close it to quit.

Everything the app creates stays in this folder:

  downloads\   beatmaps (.osz) waiting to be imported into osu!
  data\        your settings, saved osu! session and download history
  runtime\     the app's own files - don't touch

To move the app, move the whole folder. To uninstall, delete the folder.
Keep the folder private: data\config.json contains your osu! session if
"Remember me" is ticked.

Don't unzip into Program Files - the app needs to be able to write to its own
folder (if it can't, it falls back to %LOCALAPPDATA%\osu! Beatmap Downloader).

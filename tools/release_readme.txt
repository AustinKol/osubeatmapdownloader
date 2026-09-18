osu! Beatmap Downloader
=======================

1. Double-click "osu! Beatmap Downloader.exe".
   Windows may say it protected your PC - click "More info", then "Run anyway".
2. A browser tab opens with the app. Keep the black window open while downloading;
   close it to quit.

No osu! account is needed. Beatmaps are downloaded from community mirrors
(catboy.best, osudl.org, mirror.nekoha.moe, osu.direct), several at a time, so
there is no hourly limit to wait out. You can turn individual mirrors on and off
in the app.

Maps that no mirror has are collected at the end. The app can check them against
osu! itself, and if you want those too, you can sign in and let it fetch them
from osu.ppy.sh. That optional step is the only part that needs Google Chrome.

Everything the app creates stays in this folder:

  downloads\   beatmaps (.osz) waiting to be imported into osu!
  data\        your settings and the saved osu! sign-in
               if you use the optional step
  runtime\     the app's own files - don't touch

To move the app, move the whole folder. To uninstall, delete the folder.
Keep the folder private if you sign in: data\ then contains your osu! session.
Use "Sign out" in the app to remove it.

Don't unzip into Program Files - the app needs to be able to write to its own
folder (if it can't, it falls back to %LOCALAPPDATA%\osu! Beatmap Downloader).

Please be kind to the mirrors: they are volunteers paying for bandwidth.

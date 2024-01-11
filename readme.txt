Please make sure all your chrome tabs are closed before you run the code. Or else
chrome driver wouldn't work.

1. get_beatmap_id.py
	
- This will generate a beatmap_id.txt with a list of beatmap ids 
  (most played map to least played) 

- You would need to input your osu profile id, number of maps to download and 
  osu session cookie

- ex: https://osu.ppy.sh/beatmapsets/1565833#osu/3242051
      only 1565833 will be printed in the text file

2. beatmap_downloader.py

- This will read the beatmap_id.txt text file and download each beatmap in order of
  most played to least played using chrome driver

- Please enable *allow explicit content* setting on the osu website or else it won't
  download the beatmaps with uhhhh certain backgrounds yes.

- In chrome, enable *always open this type of file* for .osz files if you would like
  the beatmaps to auto-import into osu!

3. beatmap_list_checker.py

- Once the beatmaps have been downloaded, this python file can read through all the
  beatmaps in the osu song directory and export a list in beatmap_check.txt
  Order: oldest to newest beatmap downloaded (date added sort option in osu!)

- This is useful if you would like to share with your friends what beatmaps you have.
  They can potentially download them using beatmap_downloader.py

4. missing_beatmaps_check.py

- This would compare beatmap_id.txt and beatmap_check.txt and output a .txt file with
  beatmaps that have been missed during the download

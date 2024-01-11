import os
import time
 
rootdir = r'C:\Users\Komachi\Desktop\osu!\afterdataloss_songs_downloaded'

list_of_folders = []
            

with open("C:/Users/Komachi/Desktop/osu!_script/beatmap_check.txt",'a+') as f:
    for it in os.scandir(rootdir):
        list_of_folders.append(it)

    list_of_folders = sorted(list_of_folders, key = os.path.getmtime)

for folder in list_of_folders:
    with open(f"C:/Users\Komachi/Desktop/osu!/afterdataloss_songs_downloaded/{folder.name}/useless_text.txt",'a+') as f:
        f.writelines(" ")
    time.sleep(1)

            
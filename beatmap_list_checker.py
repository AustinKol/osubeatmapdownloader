import os
 
rootdir = r'D:\osu! backup (1)\Songs backup'

list_of_folders = []
beatmapnameslist = []

# with open("C:/Users/Komachi/Desktop/osu!_script/beatmap_check.txt",'a+') as f:
#     for it in os.scandir(rootdir):
#         if it.is_dir():
#             mapid = it.name.split(" ")
#             beatmapnameslist.append(mapid[0])

#     for id in beatmapnameslist:
            
#         f.writelines(str(id)+"\n")
            

with open("C:/Users/Komachi/Desktop/osu!_script/beatmap_check.txt",'a+') as f:
    for it in os.scandir(rootdir):
        list_of_folders.append(it)

    list_of_folders = sorted(list_of_folders, key = os.path.getmtime)

    for folder in list_of_folders:
        mapid = folder.name.split(" ")
        beatmapnameslist.append(mapid[0])

    for id in beatmapnameslist:
            
        f.writelines(str(id)+"\n")
            
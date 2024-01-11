beatmapscheck = []
beatmapsid = []
missingbeatmaps = []

with open("C:/Users/Komachi/Desktop/osu!_script/beatmap_check.txt",'r') as f:
    check_list = f.readlines()
    f.close

for id in check_list:
    id = id.split("\n")[0]
    beatmapscheck.append(id)

with open("C:/Users/Komachi/Desktop/osu!_script/beatmap ids/Emilico_beatmap_id.txt",'r') as f:
    id_list = f.readlines()
    f.close

for id in id_list:
    id = id.split("\n")[0]
    beatmapsid.append(id)



with open("C:/Users/Komachi/Desktop/osu!_script/beatmap_id.txt",'a+') as f:
    
    for beatmap in beatmapsid:
        if beatmap not in beatmapscheck:
            missingbeatmaps.append(beatmap) 

    for id in missingbeatmaps:  
        f.writelines(str(id)+"\n")
            
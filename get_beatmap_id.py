import json
import requests
import os
import string
import unicodedata

offsetcount = 0

while True:
    if offsetcount<=3598:
        with open("C:/Users/Emilico/Desktop/osubeatmapdownloader/beatmap_id.txt",'a+') as f:
            user_id = 14201430
            number_of_maps = 3600
            osu_session_cookie = str("eyJpdiI6IjZHRWpBaXdjc3ZIc1RqcDlKU0dqRlE9PSIsInZhbHVlIjoiK3ZmSEY1Vnh5VVFzUkxDYnVWelVMU24rcisvYWJDUnM4S09VaCs4NW1MbllTajJheVNyRmpYc0YvUkI4dm9OdURjQnp0R1gxU09Pb0hEdEJaSCthRjRSREltLzAvdVBSNURuc0VUenVlSGxsRnNEbTkyblJYc05aNStxWkoxNkRXMEF3dGdLOVZlaGFSZUFjd0NpSGFRPT0iLCJtYWMiOiI4ZmEwZTQ1MzQ0NzdmYmQwYjIwYzhlMWRiMTNmNzdmZGE2OGEyYzIxMzJhNTA3N2Q0YmFmZWNlOTFiNzNiMmY0IiwidGFnIjoiIn0%3D")

            r = requests.get(f'https://osu.ppy.sh/users/{user_id}/beatmapsets/most_played?offset={offsetcount}&limit={number_of_maps}')
            data = r.json()
            #print(data[0])
            for beatmap in data:
                beatmap_id = beatmap['beatmapset']['id']
                f.writelines(str(beatmap_id)+"\n")
            offsetcount+=100  
    else:
        f.close()
        break


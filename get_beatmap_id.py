import json
import requests
import os
import string
import unicodedata

offsetcount = 0

while True:
    if offsetcount<=3100:
        with open("C:/Users/Komachi/Desktop/osu!_script/beatmap_id.txt",'a+') as f:
            user_id = 14201430
            number_of_maps = 3022
            osu_session_cookie = str("eyJpdiI6InhQVnM1WTFOcXhWWFpJWE1rdW5vbFE9PSIsInZhbHVlIjoiUzJJdUNRWUs3L3E3WVlLSEJva0dzTERvOWhmeHJtQTlXK05GVDV4U1lRVnhqaWJEZVRxMStFajAxUCtHUkNmSUcvUVlndFErV1A5clo5V3JxdVZuWnVPa0UrK0toTGgzdXRVTnp1R0owZUcvRWZiZkRKVEkzbi9iME1PV1lmaWptY0QwQ3NrWmZWdnFKQWZvcExhSnJ3PT0iLCJtYWMiOiIwOWY1NTMzNzVhZDgwMzIxODI3ODI0NzI0M2I4MmUzOWViZThmZjViMDE4NmIzMzgyMzU0ZWY4YzVjZGRhNzdjIiwidGFnIjoiIn0%3D")

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


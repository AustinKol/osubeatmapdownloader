import requests
import os
import string
import unicodedata
import time
import json
import chromedriver_autoinstaller
import configparser

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

dirname = os.path.realpath(os.path.dirname(__file__))

def generate_config():
    config = configparser.RawConfigParser()
    config_file = f'{dirname}\config.ini'
    if os.path.exists(config_file):
        print(f"Loading config from {config_file}")
        config.read(config_file)
    else:
        config['DEFAULT'] = {
            'USER_ID': int(input("Enter your osu! user id: ")),
            'NUM_OF_MAPS': int(input("Enter the number of maps you want to download: ")),
            'OSU_SESSION_COOKIE': str(input("Enter your osu! session cookie: "))
        }
        with open(config_file, 'w') as configfile:
            print(f"Saving config to {config_file}")
            config.write(configfile)
    return config

config = generate_config()
USER_ID = int(config["DEFAULT"]["USER_ID"])
NUM_OF_MAPS = int(config["DEFAULT"]["NUM_OF_MAPS"])
OSU_SESSION_COOKIE = str(config["DEFAULT"]["OSU_SESSION_COOKIE"])
cookies = {'osu_session': OSU_SESSION_COOKIE}


def removeDisallowedFilenameChars(filename):
    validFilenameChars = "-_.() %s%s" % (string.ascii_letters, string.digits)
    cleanedFilename = unicodedata.normalize('NFKD', filename).encode('ASCII', 'ignore')
    return ''.join(chr(c) for c in cleanedFilename if chr(c) in validFilenameChars)

def setup_browser():
    chromedriver_autoinstaller.install()

    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_experimental_option('excludeSwitches', ['enable-logging'])

    prefs = {"profile.default_content_settings.popups": 0,    
        "download.default_directory":rf"{dirname}\songs", ### Set the path accordingly
        "download.prompt_for_download": False, ## change the downpath accordingly
        "download.directory_upgrade": True}
    options.add_experimental_option("prefs", prefs)

    # Open a driver, get a page, save cookies
    driver = webdriver.Chrome(options=options)
    driver.get("https://osu.ppy.sh/home")
    driver.add_cookie({'name': 'osu_session', 'value': OSU_SESSION_COOKIE})

    return driver

# Creates a list of beatmap ids from the user's most played maps and stores it in beatmap_ids.txt
def get_beatmap_id():
    # osu api only allows 100 maps per request
    # so we have to make multiple requests if the user has more than 100 maps
    # the offset is the number of maps to skip
    remaining_maps = NUM_OF_MAPS
    offset = 0
    print(f"Getting list of top {NUM_OF_MAPS} beatmaps from USER_ID: {USER_ID}")

    list_of_beatmap_ids = {}

    while remaining_maps > 0:
        limit = 100 if remaining_maps >= 100 else remaining_maps
        requesturl = f'https://osu.ppy.sh/users/{USER_ID}/beatmapsets/most_played?offset={offset}&limit={limit}'
        r = requests.get(requesturl)
        data = r.json()

        for beatmap in data:
            beatmap_id = beatmap['beatmapset']['id']
            beatmap_title = removeDisallowedFilenameChars(str(beatmap['beatmapset']['title']))
            list_of_beatmap_ids[beatmap_id] = beatmap_title

        remaining_maps -= 100
        offset += 100

    with open(f'{dirname}/beatmap_ids.txt', 'w') as file:
        json.dump(list_of_beatmap_ids, file)


def get_download_url(beatmap_id, beatmap_url):
    try: 
        r = requests.get(beatmap_url, cookies=cookies, headers = {'User-agent': 'osudownloader'})
        # Checks to see if the beatmap is downloadable, if not then use beatconnect as a fallback
        if "\"download_disabled\":true" in r.text:
            download_url = f"https://beatconnect.io/b/{beatmap_id}"
        else:
            download_url = f"{beatmap_url}#osu/"
    except Exception as e:
        download_url = ""
    return download_url

def is_beatmap_downloaded(beatmap_id, downloaded_beatmaps):
    for beatmap in downloaded_beatmaps:
        if str(beatmap_id) in beatmap:
            return True
    return False

def download_beatmaps(driver):
    unable_to_download = {}
    with open(f'{dirname}/beatmap_ids.txt', 'r') as filehandle:
        beatmaps = json.load(filehandle)

    downloaded_beatmaps = os.listdir(os.path.join(os.path.dirname(__file__), "songs"))

    print(f"Number of songs to download: {len(beatmaps)}")
    for beatmap_id, beatmap_title in beatmaps.items():
        beatmap_url = f"https://osu.ppy.sh/beatmapsets/{beatmap_id}"

        # Download only if the beatmap isn't already downloaded
        if not is_beatmap_downloaded(beatmap_id, downloaded_beatmaps):
            download_url = get_download_url(beatmap_id, beatmap_url)
            if download_url == "":
                unable_to_download[beatmap_title] = beatmap_url
                time.sleep(3)
                continue

            print(f'\n-------{beatmap_id}-------')
            print(download_url)
            print('Downloading beatmap: ' + str(beatmap_title))
            if "beatconnect" in download_url:
                try:
                    r = requests.get(download_url, headers = {'User-agent': 'osudownloader'})
                    with open(f'{dirname}/songs/{beatmap_id} {beatmap_title}.osz', 'wb') as f:  
                        f.write(r.content)
                except Exception as e:
                    unable_to_download[beatmap_title] = beatmap_url
            else:
                try:
                    if not download_from_osu(download_url, driver):
                        unable_to_download[beatmap_title] = beatmap_url
                except Exception as e:
                    unable_to_download[beatmap_title] = beatmap_url
            time.sleep(5)
    # Print out the beatmaps that couldn't be downloaded
    if unable_to_download:
        print("Unable to download these songs:")
        for beatmap_title, beatmap_url in unable_to_download.items():
            print(beatmap_title + ":" + beatmap_url)

def download_from_osu(download_url, driver):
    if len(driver.window_handles) > 1:
        driver.switch_to.window(driver.window_handles[1])
        driver.close()
        driver.switch_to.window(driver.window_handles[0])
    driver.get(download_url)
    driverwait = WebDriverWait(driver,10)

    try:
        # Downloads the beatmap without the video
        driverwait.until(EC.element_to_be_clickable((By.XPATH, "//*[text()[contains(.,'without')]]"))).click()
    except Exception as e:
        try:
            # Clicks the download button if the without video button does not exist
            driverwait.until(EC.element_to_be_clickable((By.XPATH, "//*[text()[contains(.,'Download')]]"))).click()
            return True
        except Exception as e:
            return False
        return False
    return True

if __name__ == "__main__":
    if not os.path.isfile(f'{dirname}/beatmap_ids.txt'):
        get_beatmap_id()

    print(f"Download directory is stored in: {dirname}/songs")
    os.makedirs(f"{dirname}/songs", exist_ok = True)
    
    driver = setup_browser()
    download_beatmaps(driver)
    time.sleep(15)
    driver.close()
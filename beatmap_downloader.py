from ast import main
import time 
from selenium import webdriver
from selenium.webdriver import ChromeOptions
from selenium.webdriver.common.by import By

url_list=[]
with open("C:/Users/austi/Desktop/osu!_script/beatmap_id.txt",'r') as f:
    id_list = f.readlines()
    f.close


def download(url_list):
    count = 0
    option = webdriver.ChromeOptions()
    option.add_argument("--user-data-dir=C:/Users/austi/AppData/Local/Google/Chrome/User Data/")
    driver = webdriver.Chrome(executable_path="C:/Users/austi/Desktop/osu!_script/chromedriver.exe", chrome_options=option)
    for url in url_list:
        if len(driver.window_handles) > 1:
            driver.switch_to.window(driver.window_handles[1])
            driver.close()
            driver.switch_to.window(driver.window_handles[0])
        if count==60:
            print("15s break!")
            time.sleep(10)
            count=0
        driver.get(url)
        files = driver.find_elements(by=By.CLASS_NAME,value="btn-osu-big__content")
        if len(files) == 4:
            files[1].click()
        elif len(files) == 5:
            files[2].click()
        time.sleep(3)
        count+=1
        print(f"finish download: {url}")
    time.sleep(15)
    driver.close()

def get_link(id):
    link = f"https://osu.ppy.sh/beatmapsets/{id}#osu/"
    return(link)

for id in id_list:
    id = id.split("\n")[0]
    link = get_link(id)
    url_list.append(link)
    
download(url_list)








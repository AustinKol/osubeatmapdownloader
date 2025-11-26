import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

def main():
    options = webdriver.ChromeOptions()

    # Important for some recent Chrome/Selenium combos
    options.add_argument("--remote-allow-origins=*")

    # Start Chrome with an auto-matched ChromeDriver
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )

    driver.get("https://www.google.com")
    print("Opened Google.")
    time.sleep(5)
    driver.quit()
    print("Closed Chrome.")

if __name__ == "__main__":
    main()

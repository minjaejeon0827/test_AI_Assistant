import os
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

APP_URL = os.environ["STREAMLIT_APP_URL"]

options = Options()
options.add_argument("--headless=new")        # 화면 없이 실행
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--window-size=1280,800")

driver = webdriver.Chrome(options=options)
try:
    driver.get(APP_URL)
    try:
        # 잠든 상태면 '깨우기' 버튼이 나타남
        button = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[contains(., 'get this app back up')]")
            )
        )
        print("잠든 앱 감지 → 깨우기 버튼 클릭")
        button.click()
        time.sleep(60)   # 앱 재시작 대기
    except TimeoutException:
        print("앱이 이미 깨어 있음 → 잠시 머무르며 방문 기록 남기기")
        time.sleep(20)

    driver.save_screenshot("screenshot.png")  # 결과 확인용
finally:
    driver.quit()
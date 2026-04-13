import sys
import os
import time
from datetime import datetime

import undetected_chromedriver as uc

DEBUG_DIR = "debug_test"
os.makedirs(DEBUG_DIR, exist_ok=True)

def get_next_run_dir():
    run_num = 1
    while True:
        run_dir = os.path.join(DEBUG_DIR, f"run#{run_num}")
        if not os.path.exists(run_dir):
            os.makedirs(run_dir, exist_ok=True)
            return run_dir
        run_num += 1

def save_debug(driver, label, run_dir):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.join(run_dir, f"{label}_{ts}")

    try:
        # Screenshot
        driver.save_screenshot(base + ".png")

        # HTML
        with open(base + ".html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)

        # Visible text
        text = driver.execute_script("return document.body.innerText || ''")
        with open(base + ".txt", "w", encoding="utf-8") as f:
            f.write(text)

        # URL
        with open(base + "_url.txt", "w") as f:
            f.write(driver.current_url)

        print(f"[DEBUG] Saved snapshot: {base}")

    except Exception as e:
        print(f"[ERROR] Debug save failed: {e}")


def is_blocked(driver):
    try:
        text = driver.execute_script("return document.body.innerText || ''").lower()
        return any(x in text for x in [
            "you can't join this video call",
            "return to home screen",
            "not allowed to join",
        ])
    except:
        return False


def main():
    if len(sys.argv) < 2:
        print("Usage: python testing.py <google_meet_url>")
        sys.exit(1)

    url = sys.argv[1]

    print(f"[INFO] Opening: {url}")

    options = uc.ChromeOptions()

    # ⚠️ Exact identical arguments as meet_bot.py
    # options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,720")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--autoplay-policy=no-user-gesture-required")
    options.add_argument("--use-fake-ui-for-media-stream")
    options.add_argument("--use-fake-device-for-media-stream")

    # Generate a unique temp profile for this test run exactly like the Bot
    run_dir = get_next_run_dir()
    temp_profile = os.path.join(run_dir, "test_profile")
    options.add_argument(f"--user-data-dir={temp_profile}")

    options.add_experimental_option("prefs", {
        "profile.default_content_setting_values.media_stream_mic": 1,
        "profile.default_content_setting_values.media_stream_camera": 1,
        "profile.default_content_setting_values.notifications": 1,
    })

    # Enable logs
    options.set_capability("goog:loggingPrefs", {
        "browser": "ALL"
    })

    driver = uc.Chrome(options=options, version_main=146)

    print(f"[INFO] Saving absolute telemetry into: {run_dir}/")

    try:
        driver.get(url)
        time.sleep(5)

        save_debug(driver, "after_load", run_dir)

        # Check blocked
        if is_blocked(driver):
            print("[RESULT] ❌ BLOCKED PAGE DETECTED")
        else:
            print("[RESULT] ✅ Not blocked (maybe lobby or other state)")

        # Dump browser logs
        try:
            logs = driver.get_log("browser")
            with open(os.path.join(run_dir, "browser_logs.txt"), "w") as f:
                for log in logs:
                    f.write(str(log) + "\n")
        except Exception:
            pass

        print(f"[INFO] Done. Thoroughly check out the {run_dir}/ folder.")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
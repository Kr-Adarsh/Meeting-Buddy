"""
Meeting Buddy — Google Meet Bot
Joins a meeting, records audio, and scrapes live captions.
Designed for local development on Linux with PulseAudio/PipeWire.
"""

import subprocess
import os
import time
import threading
from datetime import datetime

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, NoSuchElementException

RECORDINGS_DIR = "recordings"
DEBUG_DIR = "debug_screenshots"
BOT_NAME = "Meeting Buddy"


class MeetBot:
    """Selenium-based Google Meet bot that records audio and captures captions."""

    def __init__(self, meet_url: str, job_id: str, bot_name: str = BOT_NAME):
        self.meet_url = meet_url
        self.job_id = job_id
        self.bot_name = bot_name

        self.driver = None
        self.ffmpeg_process = None
        self.stop_event = threading.Event()
        self.captions = []
        self.start_time = None
        self.status = "pending"

        # Paths
        self.job_dir = os.path.join(RECORDINGS_DIR, job_id)
        self.audio_file = os.path.join(self.job_dir, "audio.wav")
        self.captions_file = os.path.join(self.job_dir, "captions.txt")
        self._temp_audio = os.path.join(self.job_dir, "_temp_audio.wav")
        self.sink_name = f"MeetBotSink_{job_id}"
        self.sink_module_id = None

    def stop(self):
        """Signal the bot to stop recording and leave."""
        self._log("🛑 Stop signal received")
        self.stop_event.set()

    def _should_stop(self):
        """Check if stop was requested. Call this frequently."""
        return self.stop_event.is_set()

    #  MAIN ENTRY POINT

    def run(self) -> dict:
        """Execute the full bot lifecycle. Returns a result dict."""
        os.makedirs(self.job_dir, exist_ok=True)
        os.makedirs(DEBUG_DIR, exist_ok=True)

        try:
            # — Join —
            self.status = "joining"
            self._log("Starting Meeting Buddy", f"URL: {self.meet_url}")
            self._launch_chrome()
            self._debug_snapshot("00_after_launch")

            if self._should_stop():
                raise Exception("Stopped by user before joining")

            self._navigate_to_meet()
            self._debug_snapshot("01_after_navigation")
            
            self._dismiss_dialogs()
            self._debug_snapshot("02_after_dialogs")

            if self._should_stop():
                raise Exception("Stopped by user before joining")

            # Check if we're already in the meeting (signed-in user auto-joins)
            if self._is_in_meeting():
                self._log("Already in meeting (signed-in user)")
            else:
            # Guest flow: enter name, turn off mic/cam, click join
                self._enter_name()
                self._debug_snapshot("03_after_name")
                
                self._turn_off_mic_cam()
                self._debug_snapshot("04_before_join")

                if self._should_stop():
                    raise Exception("Stopped by user before joining")

                if self._is_blocked():
                    self._debug_snapshot("BLOCKED")
                    raise Exception("Blocked by Google Meet")

                self._click_join()
                self._wait_for_admission()

            # Turn off mic/cam once inside
            self._turn_off_mic_cam()
            self._screenshot("04_in_meeting")

            # — Record —
            self.status = "recording"
            self.start_time = time.time()
            self._log("🎙️ Starting recording...")
            self._start_audio_recording()
            time.sleep(2)
            self._enable_captions()
            self._inject_caption_observer()
            self._monitor_meeting()

            # — Finalize —
            self.status = "completed"
            self._finalize()
            return self._build_result()

        except Exception as e:
            self.status = "failed"
            self._log("FATAL", str(e))
            self._debug_snapshot("99_failure_state")
            self._finalize()
            result = self._build_result()
            result["error"] = str(e)
            return result

        finally:
            self._cleanup()

    #  CHROME LIFECYCLE

    def _detect_chrome_version(self):
        """Detect installed Chrome major version to match ChromeDriver."""
        for binary in [
            "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium-browser", "/usr/bin/chromium",
        ]:
            if os.path.exists(binary):
                try:
                    out = subprocess.check_output(
                        [binary, "--version"], timeout=5
                    ).decode().strip()
                    ver = int(out.split()[-1].split(".")[0])
                    self._log(f"Chrome detected: v{ver} ({binary})")
                    return ver, binary
                except Exception:
                    pass
        return None, None

    def _create_null_sink(self):
        try:
            # Completely abandon the system PulseAudio which is blocking our XVFB connection via strict Session bindings.
            # Instead, dynamically summon a totally isolated PulseAudio server daemon per-bot exclusively via UNIX socket!
            self.pulse_dir = os.path.join(DEBUG_DIR, f"pulse_{self.job_id}")
            os.makedirs(self.pulse_dir, exist_ok=True)
            self.pulse_socket = os.path.join(self.pulse_dir, "pulse.sock")

            # Native command forcefully loading absolute minimum modules without probing hardware
            cmd_str = (
                f'pulseaudio -n '
                f'--load="module-native-protocol-unix auth-anonymous=1 socket={self.pulse_socket}" '
                f'--load="module-null-sink sink_name={self.sink_name}" '
                f'--exit-idle-time=-1 '
                f'--daemonize=false'
            )

            my_env = os.environ.copy()
            my_env["PULSE_CONFIG_PATH"] = self.pulse_dir
            my_env["PULSE_RUNTIME_PATH"] = self.pulse_dir
            my_env["XDG_RUNTIME_DIR"] = self.pulse_dir

            self.pulse_process = subprocess.Popen(
                cmd_str, env=my_env, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

            # Polling for Socket presence
            for _ in range(30):
                if os.path.exists(self.pulse_socket):
                    self._log(f"Isolated Audio Daemon securely mounted to UNIX socket!")
                    # Force Chrome and FFmpeg internally to route exclusively entirely into this exact socket!
                    os.environ["PULSE_SERVER"] = f"unix:{self.pulse_socket}"
                    os.environ["PULSE_SINK"] = self.sink_name
                    self.sink_module_id = "isolated_pulse_daemon"
                    return True
                time.sleep(0.1)

            self._log("⚠️ Isolated PulseAudio Daemon failed to map socket.")

        except Exception as e:
            self._log(f"⚠️ Could not create isolated sink: {e}")
        return False

    def _destroy_null_sink(self):
        if hasattr(self, 'pulse_process') and self.pulse_process.poll() is None:
            try:
                self.pulse_process.terminate()
                self.pulse_process.wait(timeout=3)
            except Exception:
                self.pulse_process.kill()
            self._log("Isolated virtual audio daemon wiped.")
            
        if "PULSE_SERVER" in os.environ:
            del os.environ["PULSE_SERVER"]
        if "PULSE_SINK" in os.environ:
            del os.environ["PULSE_SINK"]

    def _launch_chrome(self):
        chrome_version, chrome_binary = self._detect_chrome_version()
        self._create_null_sink()

        options = uc.ChromeOptions()
        # options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1280,720")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--autoplay-policy=no-user-gesture-required")
        options.add_argument("--use-fake-ui-for-media-stream")
        options.add_argument("--use-fake-device-for-media-stream")
        # We removed --mute-audio so ffmpeg can successfully capture the bot's speaker output!
        # If the user hears an echo, it's because BOTH their main Chrome and the Bot's Chrome are playing audio.
        # The fix for the user is to right-click their personal Meet tab and "Mute Site" while the bot records.
        
        # Force a clean profile every time so it DOES NOT sign in automatically.
        # This guarantees it goes to the "What's your name?" / "Ask to Join" screen.
        temp_profile = os.path.join(DEBUG_DIR, f"profile_{self.job_id}")
        options.add_argument(f"--user-data-dir={temp_profile}")

        options.add_experimental_option("prefs", {
            "profile.default_content_setting_values.media_stream_mic": 1,
            "profile.default_content_setting_values.media_stream_camera": 1,
            "profile.default_content_setting_values.notifications": 1,
        })

        if chrome_binary:
            options.binary_location = chrome_binary

        self.driver = uc.Chrome(
            options=options,
            version_main=chrome_version,
        )
        self.driver.implicitly_wait(0.5)
        self._log("Chrome launched")

    def _navigate_to_meet(self):
        self.driver.get(self.meet_url)
        # Reduced initial wait. The implicit wait + polling in the next steps will handle readiness.
        time.sleep(2)  
        url = self.driver.current_url
        self._log("Page loaded", url)
        self._screenshot("01_loaded")
        if "meet.google.com" not in url:
            raise Exception(f"Redirected away from Meet: {url}")

    #  MEETING JOIN FLOW

    def _dismiss_dialogs(self):
        js_code = """
            const phrases = ["got it", "dismiss", "accept all", "accept", "continue without signing in", "skip", "ok", "close"];
            let clicked = false;
            document.querySelectorAll('button').forEach(btn => {
                const t = (btn.textContent || '').toLowerCase().trim();
                if (btn.offsetParent !== null && phrases.includes(t)) {
                    btn.click();
                    clicked = true;
                }
            });
            return clicked;
        """
        try:
            if self.driver.execute_script(js_code):
                self._log("Dismissed dialogs via JS")
        except Exception:
            pass

    def _enter_name(self):
        """Enter bot name reliably via JS using exact DOM classes, with polling."""
        js_script = """
            let input = document.querySelector('input[jsname="YPqjbf"]') || 
                        document.querySelector('input[aria-label="Your name"]');
            
            if (input && input.offsetParent !== null) {
                input.focus();
                let nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
                nativeInputValueSetter.call(input, arguments[0]);
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
                
                input.value += ' ';
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.value = input.value.slice(0, -1);
                input.dispatchEvent(new Event('input', { bubbles: true }));
                
                return true;
            }
            return false;
        """
        # Poll rapidly up to 10 seconds for the element to appear
        for _ in range(20):
            try:
                success = self.driver.execute_script(js_script, self.bot_name)
                if success:
                    self._log(f"✏️ Name entered via JS: {self.bot_name}")
                    return
            except Exception:
                pass
            time.sleep(0.5)
            
        self._log("ℹ️ No name field found (bot may be signed in already)")

    def _turn_off_mic_cam(self):
        """Mute mic and camera instantly via JS."""
        js_code = """
            let actions = [];
            // Mic
            let mics = document.querySelectorAll('[aria-label*="Turn off microphone"]');
            if (mics.length > 0) {
                mics.forEach(m => m.click());
                actions.push("🎤 Mic off");
            }
            // Camera
            let cams = document.querySelectorAll('[aria-label*="Turn off camera"]');
            if (cams.length > 0) {
                cams.forEach(c => c.click());
                actions.push("📷 Camera off");
            }
            return actions;
        """
        try:
            actions = self.driver.execute_script(js_code)
            for action in actions:
                self._log(action)
        except Exception:
            pass

    def _click_join(self):
        """Click join button. Checks stop_event between retries."""
        selectors = [
            (By.CSS_SELECTOR, 'button[jsname="Qx7uuf"]'),
            (By.CSS_SELECTOR, 'button[aria-label*="Ask to join"]'),
            (By.CSS_SELECTOR, 'button[aria-label*="Join now"]'),
            (By.XPATH, '//button[contains(text(), "Ask to join")]'),
            (By.XPATH, '//button[contains(text(), "Join now")]'),
            (By.XPATH, '//button[.//span[contains(text(), "Ask to join")]]'),
            (By.XPATH, '//button[.//span[contains(text(), "Join now")]]'),
        ]

        for attempt in range(3):
            if self._should_stop():
                raise Exception("Stopped by user during join")

            # Check if we've ALREADY joined (e.g. auto-join for signed-in users)
            if self._is_in_meeting():
                self._log("Already in meeting!")
                return

            # Try each selector
            for by, sel in selectors:
                try:
                    btn = self.driver.find_element(by, sel)
                    if btn.is_displayed() and btn.is_enabled():
                        btn.click()
                        self._log(f"Clicked join button")
                        time.sleep(0.5)
                        return
                except Exception:
                    pass

            # Extremely aggressive Native DOM approach
            try:
                result = self.driver.execute_script("""
                    function forceClick(node) {
                        if (!node) return false;
                        node.click();
                        node.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true, view: window}));
                        node.dispatchEvent(new MouseEvent('mouseup',   {bubbles: true, cancelable: true, view: window}));
                        node.dispatchEvent(new MouseEvent('click',     {bubbles: true, cancelable: true, view: window}));
                        return true;
                    }
                    
                    // 1. Direct class match based on user HTML
                    let btn = document.querySelector('button.UywwFc-LgbsSe');
                    if (btn && btn.offsetParent !== null && (btn.innerText.includes('Ask to join') || btn.innerText.includes('Join now'))) {
                        forceClick(btn);
                        return btn.innerText.trim();
                    }
                    
                    // 2. Scan all SPAN nodes directly for "Ask to join" text (handles nested structures)
                    let spans = Array.from(document.querySelectorAll('span, div[jsname="V67aGc"]')).filter(s => 
                        (s.innerText || '').trim() === 'Ask to join' || 
                        (s.innerText || '').trim() === 'Join now'
                    );
                    if (spans.length > 0) {
                        let p = spans[0].closest('button') || spans[0].closest('div[role="button"]') || spans[0];
                        forceClick(p);
                        return spans[0].innerText.trim();
                    }

                    // 3. Scan all generic buttons
                    let buttons = document.querySelectorAll('button, div[role="button"]');
                    for (let b of buttons) {
                        let text = (b.textContent || '').toLowerCase().trim();
                        if ((text.includes('ask to join') || text.includes('join now') 
                             || text.includes('join') || text.includes('participate')) 
                             && b.offsetParent !== null) {
                            forceClick(b);
                            return text;
                        }
                    }
                    return null;
                """)
                if result:
                    self._log(f"Joined via aggressive DOM injection: '{result}'")
                    time.sleep(1)
                    return
            except Exception as e:
                self._log(f"JS join error: {e}")

            if attempt < 2:
                self._log(f"Join attempt {attempt+1}/3 failed, retrying...")
                self._screenshot(f"join_retry_{attempt+1}")
                self._dismiss_dialogs()
                # Interruptible sleep — check stop every 0.5s
                for _ in range(6):
                    if self._should_stop():
                        raise Exception("Stopped by user during join")
                    time.sleep(0.5)

        # Final check — maybe we DID join but didn't detect it
        if self._is_in_meeting():
            self._log("Already in meeting (detected after retries)")
            return

        self._screenshot("join_FAILED")
        raise Exception("Failed to click Join button after all attempts")

    def _wait_for_admission(self, timeout=180):
        """Wait until admitted. Checks stop_event."""
        self._log(f"⏳ Waiting for admission ({timeout}s)...")
        self._log("Please admit 'Meeting Buddy' from your Meet window!")
        self._screenshot("03_waiting_admission")

        start = time.time()
        while time.time() - start < timeout:
            if self._should_stop():
                raise Exception("Stopped by user while waiting for admission")
            if self._is_in_meeting():
                self._log("Admitted to meeting!")
                return
            time.sleep(1)

        self._screenshot("admission_timeout")
        raise Exception(f"Not admitted within {timeout}s")

    def _is_in_meeting(self):
        """Check if bot is inside an active meeting."""
        for sel in [
            'button[aria-label*="Leave call"]',
            'button[aria-label*="Show everyone"]',
            'button[aria-label*="Present now"]',
            '[data-call-ended="false"]',
        ]:
            try:
                for el in self.driver.find_elements(By.CSS_SELECTOR, sel):
                    if el.is_displayed():
                        return True
            except Exception:
                pass

        # JS fallback — check for in-meeting indicators
        try:
            return bool(self.driver.execute_script("""
                const btns = Array.from(document.querySelectorAll('button'));
                return btns.some(b => {
                    const label = (b.getAttribute('aria-label') || '').toLowerCase();
                    return label.includes('leave call') || label.includes('show everyone')
                           || label.includes('present now');
                });
            """))
        except Exception:
            return False

    #  AUDIO RECORDING  (FFmpeg + PulseAudio)

    def _start_audio_recording(self):
        # Fallback to default if sink didn't create
        source = f"{self.sink_name}.monitor" if self.sink_module_id else "default"
        
        cmd = [
            "ffmpeg", "-y",
            "-f", "pulse", "-i", source,
            "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            self._temp_audio,
        ]
        self.ffmpeg_process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._log(f"🎙️ Audio recording started on {source}")

    def _stop_audio_recording(self):
        if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
            self.ffmpeg_process.terminate()
            try:
                self.ffmpeg_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.ffmpeg_process.kill()
        if os.path.exists(self._temp_audio):
            os.rename(self._temp_audio, self.audio_file)
            size_mb = os.path.getsize(self.audio_file) / (1024 * 1024)
            self._log(f"💾 Audio saved ({size_mb:.1f} MB)")

    #  CAPTIONS SCRAPING

    def _enable_captions(self):
        """Force captions on via Selenium explicitly."""
        self._log("Attempting to enable captions...")
        for _ in range(15): # Try for 7.5 seconds
            try:
                # First check if it's already on
                try:
                    btn_off = self.driver.find_element(By.CSS_SELECTOR, 'button[aria-label="Turn off captions"]')
                    if btn_off.is_displayed():
                        self._log("💬 Captions are already enabled")
                        return
                except Exception:
                    pass

                # Then look for the turn ON button
                try:
                    btn_on = self.driver.find_element(By.CSS_SELECTOR, 'button[aria-label="Turn on captions"], button[data-tooltip*="Turn on captions"]')
                    if btn_on.is_displayed():
                        btn_on.click()
                        self._log("💬 Captions enabled (UI button clicked natively)")
                        return
                except Exception:
                    pass

            except Exception:
                pass
            time.sleep(0.5)
            
        # Keyboard fallback
        try:
            self.driver.execute_script("document.body.dispatchEvent(new KeyboardEvent('keydown', {'key':'c'}));")
            self._log("💬 Captions enabled (JS Keyboard event 'c')")
        except Exception:
            pass

    def _inject_caption_observer(self):
        """Inject an interval-based scraper that tags Google Meet DOM blocks by ID."""
        try:
            self.driver.execute_script("""
                window._mbCaptionsDict = window._mbCaptionsDict || {};
                
                function fetchCaps() {
                    const region = document.querySelector('div[role="region"][aria-label="Captions"]');
                    if (!region) return;
                    
                    const blocks = region.querySelectorAll('.nMcdL');
                    blocks.forEach(block => {
                        // Tag the DOM node with a unique ID so we track its growth
                        if (!block.dataset.mbId) {
                            block.dataset.mbId = 'mb_' + Date.now() + '_' + Math.floor(Math.random()*10000);
                        }
                        
                        const id = block.dataset.mbId;
                        const speakerEl = block.querySelector('.NWpY1d, .KcIKyf');
                        const speaker = speakerEl ? speakerEl.textContent.trim() : 'Unknown';
                        
                        const textEls = block.querySelectorAll('.ygicle, .VbkSUe');
                        let text = Array.from(textEls).map(el => el.textContent.trim()).join(' ').trim();
                        
                        if (text.length > 2) {
                            if (!window._mbCaptionsDict[id]) {
                                window._mbCaptionsDict[id] = {
                                    id: id,
                                    speaker: speaker,
                                    timestamp: new Date().toISOString()
                                };
                            }
                            window._mbCaptionsDict[id].text = text;
                        }
                    });
                }
                
                setInterval(fetchCaps, 500); 
            """)
            self._log("💬 Caption observer injected (DOM tracking)")
        except Exception as e:
            self._log(f"⚠️ Caption observer failed: {e}")

    def _collect_captions(self):
        """Sync Javascript tracking dictionary with Python."""
        try:
            caps_dict = self.driver.execute_script("return window._mbCaptionsDict || {};")
            if caps_dict:
                if not hasattr(self, 'captions_dict'):
                    self.captions_dict = {}
                
                # Upsert into python tracking dict
                self.captions_dict.update(caps_dict)
                
                # Rebuild linear array chronologically
                self.captions = list(self.captions_dict.values())
                self.captions.sort(key=lambda x: x.get("timestamp", ""))
        except Exception:
            pass



    #  MEETING MONITORING

    def _monitor_meeting(self):
        self._log("📍 Recording... (stop via UI or wait for meeting end)")
        tick = 0
        while not self._should_stop():
            time.sleep(3)
            tick += 1
            self._collect_captions()

            if self._has_meeting_ended():
                self._log("🔔 Meeting ended")
                return

            if tick % 20 == 0:
                elapsed = tick * 3
                self._log(f"📍 Still recording ({elapsed}s, {len(self.captions)} captions)")

        self._log("🛑 Stopped by user")

    def _has_meeting_ended(self):
        try:
            ended = self.driver.execute_script("""
                const body = (document.body.innerText || '').toLowerCase();
                const phrases = [
                    'the meeting has ended', 'meeting ended',
                    'you left the meeting', "you've been removed",
                    'return to home screen', 'you have been disconnected'
                ];
                for (const p of phrases) { if (body.includes(p)) return p; }
                if (!document.querySelector('button[aria-label*="Leave call"]'))
                    return 'no_leave_button';
                return null;
            """)
            return ended is not None
        except Exception:
            return True

    #  FINALIZE & CLEANUP

    def _finalize(self):
        self._stop_audio_recording()
        self._collect_captions()
        
        # Save Captions
        if hasattr(self, 'captions') and self.captions:
            with open(self.captions_file, "w", encoding="utf-8") as f:
                for cap in self.captions:
                    ts = cap.get('timestamp', '')[:19] # Trim timestamp nicely
                    speaker = cap.get('speaker', 'Unknown')
                    f.write(f"[{ts}] {speaker}: {cap.get('text','')}\n")
            self._log(f"💾 Captions saved ({len(self.captions)} entries)")
            
        # Save Metadata Ledger
        import json
        meta = {
            "job_id": self.job_id,
            "url": self.meet_url,
            "status": "completed" if self.status != "failed" else "failed",
            "duration_seconds": int(time.time() - self.start_time) if self.start_time else 0,
            "captions_count": len(self.captions) if hasattr(self, 'captions') else 0,
            "has_audio": os.path.exists(self.audio_file),
            "has_captions": os.path.exists(self.captions_file)
        }
        with open(os.path.join(self.job_dir, "metadata.json"), "w") as f:
            json.dump(meta, f)

    def _build_result(self) -> dict:
        duration = int(time.time() - self.start_time) if self.start_time else 0
        return {
            "status": self.status,
            "audio_file": self.audio_file if os.path.exists(self.audio_file) else None,
            "captions_file": self.captions_file if os.path.exists(self.captions_file) else None,
            "captions": self.captions,
            "captions_count": len(self.captions),
            "duration_seconds": duration,
        }

    def _leave_meeting(self):
        """Explicitly press the 'Leave call' button so it leaves the meet immediately."""
        self._log("Attempting to explicitly leave meeting...")
        try:
            self.driver.execute_script("""
                // Try clicking the red phone icon explicitly
                let leaveBtns = document.querySelectorAll('button[aria-label*="Leave call"], button[aria-label*="leave"]');
                for (let b of leaveBtns) {
                    if (b.offsetParent) {
                        b.click();
                        return true;
                    }
                }
                return false;
            """)
            time.sleep(1) # wait for the leave animation
        except Exception:
            pass

    def _cleanup(self):
        if self.ffmpeg_process and self.ffmpeg_process.poll() is None:
            self.ffmpeg_process.terminate()
            try:
                self.ffmpeg_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.ffmpeg_process.kill()
        if self.driver:
            try:
                self._leave_meeting() # Hang up first
            except Exception:
                pass
                
            try:
                self.driver.close() # Close tab first
                time.sleep(0.5)
            except Exception:
                pass
                
            try:
                self.driver.quit() # Fully kill chrome process
                self._log("Browser closed")
            except Exception:
                pass
        
        self._destroy_null_sink()

    def _screenshot(self, name):
        try:
            path = os.path.join(DEBUG_DIR, f"{self.job_id}_{name}.png")
            self.driver.save_screenshot(path)
        except Exception:
            pass

    def _debug_snapshot(self, label):
        try:
            ts = int(time.time())
            
            # Make sure per-run directory exists
            run_dir = os.path.join(DEBUG_DIR, self.job_id)
            os.makedirs(run_dir, exist_ok=True)
            
            base = os.path.join(run_dir, f"{self.job_id}_{label}_{ts}")

            # Screenshot
            self.driver.save_screenshot(base + ".png")

            # Page HTML
            with open(base + ".html", "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)

            # Visible text
            text = self.driver.execute_script("return document.body.innerText || ''")
            with open(base + ".txt", "w", encoding="utf-8") as f:
                f.write(text)

            # URL
            with open(base + "_url.txt", "w") as f:
                f.write(self.driver.current_url)

            self._log(f"📸 Debug snapshot saved: {label}")

        except Exception as e:
            self._log(f"Debug snapshot failed: {e}")

    def _is_blocked(self):
        try:
            text = self.driver.execute_script("return document.body.innerText || ''").lower()
            return any(x in text for x in [
                "you can't join this video call",
                "return to home screen",
                "not allowed to join",
            ])
        except Exception:
            return False

    def _log(self, msg, detail=""):
        prefix = f"[BOT:{self.job_id}]"
        print(f"{prefix} {msg} {detail}".strip())

#  Standalone tes
if __name__ == "__main__":
    import json
    url = input("Paste a Google Meet link: ").strip()
    bot = MeetBot(meet_url=url, job_id="test")
    result = bot.run()
    print(json.dumps(result, indent=2, default=str))

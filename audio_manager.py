from __future__ import annotations

import os
import time
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Optional, Iterator


class AudioManagerError(RuntimeError):
    pass


@dataclass
class BotAudioSession:
    """
    Per-bot audio session:
      - one unique null sink per meeting
      - Chrome is launched with PULSE_SINK pointed at that sink
      - ffmpeg records only <sink>.monitor
      - cleanup unloads the sink module
    """

    job_id: str
    recordings_dir: str = "recordings"
    debug_dir: str = "debug_screenshots"
    sink_prefix: str = "MeetBotSink"
    pulse_server: Optional[str] = None

    sink_name: str = field(init=False)
    sink_module_id: Optional[int] = field(default=None, init=False)
    audio_process: Optional[subprocess.Popen] = field(default=None, init=False)

    job_dir: Path = field(init=False)
    audio_file: Path = field(init=False)
    temp_audio_file: Path = field(init=False)

    _env_lock = threading.Lock()

    def __post_init__(self) -> None:
        self.sink_name = f"{self.sink_prefix}_{self.job_id}"
        self.job_dir = Path(self.recordings_dir) / self.job_id
        self.job_dir.mkdir(parents=True, exist_ok=True)
        Path(self.debug_dir).mkdir(parents=True, exist_ok=True)
        self.audio_file = self.job_dir / "audio.wav"
        self.temp_audio_file = self.job_dir / "_temp_audio.wav"

    def chrome_env_updates(self) -> Dict[str, str]:
        updates = {"PULSE_SINK": self.sink_name}
        if self.pulse_server:
            updates["PULSE_SERVER"] = self.pulse_server
        return updates

    @contextmanager
    def patch_env_for_chrome(self) -> Iterator[None]:
        """
        Temporarily patch os.environ for Selenium/Chrome launch only.
        This is thread-safe enough for multi-bot launches because we lock
        around the short launch window.
        """
        updates = self.chrome_env_updates()
        with self._env_lock:
            old_values: Dict[str, Optional[str]] = {k: os.environ.get(k) for k in updates}
            try:
                os.environ.update(updates)
                yield
            finally:
                for key, old in old_values.items():
                    if old is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = old

    def ffmpeg_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        env["PULSE_SINK"] = self.sink_name
        if self.pulse_server:
            env["PULSE_SERVER"] = self.pulse_server
        return env

    def _run(self, args: list[str], timeout: int = 10, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env or os.environ.copy(),
        )

    def ensure_pulse_access(self) -> None:
        """
        Ensure pactl can talk to some PulseAudio server.
        If not, try to start a user daemon as a best-effort fallback.
        """
        check = self._run(["pactl", "info"], timeout=6)
        if check.returncode == 0:
            return

        # Best-effort bootstrap. This is not the main architecture; it is only a fallback.
        self._run(["pulseaudio", "--start"], timeout=10)
        time.sleep(0.5)

        check = self._run(["pactl", "info"], timeout=6)
        if check.returncode == 0:
            return

        raise AudioManagerError(
            "PulseAudio is not reachable. pactl cannot connect to a server. "
            f"Last error: {check.stderr.strip() or check.stdout.strip() or 'unknown'}"
        )

    def create_sink(self) -> None:
        """
        Create a per-bot null sink and verify its monitor source exists.
        """
        self.ensure_pulse_access()

        result = self._run(
            [
                "pactl", "load-module", "module-null-sink",
                f"sink_name={self.sink_name}",
                f"sink_properties=device.description={self.sink_name}",
            ],
            timeout=10,
        )

        if result.returncode != 0:
            raise AudioManagerError(
                f"Failed to create sink {self.sink_name}: {result.stderr.strip() or result.stdout.strip()}"
            )

        module_id_text = result.stdout.strip()
        if not module_id_text.isdigit():
            raise AudioManagerError(
                f"PulseAudio returned an invalid module id for sink {self.sink_name}: {module_id_text!r}"
            )

        self.sink_module_id = int(module_id_text)

        # Verify sink and monitor exist before we continue.
        self.validate_sink()

    def validate_sink(self) -> None:
        sinks = self._run(["pactl", "list", "short", "sinks"], timeout=6)
        if self.sink_name not in sinks.stdout:
            raise AudioManagerError(
                f"Sink {self.sink_name} was not found after creation. "
                f"pactl sinks:\n{sinks.stdout}\n{sinks.stderr}"
            )

        sources = self._run(["pactl", "list", "short", "sources"], timeout=6)
        monitor_name = f"{self.sink_name}.monitor"
        if monitor_name not in sources.stdout:
            raise AudioManagerError(
                f"Monitor source {monitor_name} was not found after creation. "
                f"pactl sources:\n{sources.stdout}\n{sources.stderr}"
            )

    def prepare_for_browser(self) -> None:
        """
        Call this before launching Chrome.
        It creates the sink and then you wrap the Chrome launch in patch_env_for_chrome().
        """
        self.create_sink()

    def start_recording(self) -> None:
        if self.sink_module_id is None:
            raise AudioManagerError("Cannot start recording: sink has not been created.")

        if self.audio_process and self.audio_process.poll() is None:
            return

        source = f"{self.sink_name}.monitor"

        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f", "pulse",
            "-i", source,
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            str(self.temp_audio_file),
        ]

        self.audio_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=self.ffmpeg_env(),
        )

        # Give ffmpeg a moment to initialize, and fail fast if it exits immediately.
        time.sleep(0.5)
        if self.audio_process.poll() is not None:
            raise AudioManagerError(
                f"ffmpeg exited immediately while opening {source}. "
                "That usually means the PulseAudio sink/monitor is not usable."
            )

    def stop_recording(self) -> Optional[Path]:
        if self.audio_process and self.audio_process.poll() is None:
            self.audio_process.terminate()
            try:
                self.audio_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.audio_process.kill()
                try:
                    self.audio_process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass

        self.audio_process = None

        if self.temp_audio_file.exists() and self.temp_audio_file.stat().st_size > 0:
            if self.audio_file.exists():
                self.audio_file.unlink()
            shutil.move(str(self.temp_audio_file), str(self.audio_file))
            return self.audio_file

        # Clean up empty temp file if ffmpeg created one but wrote nothing.
        if self.temp_audio_file.exists():
            self.temp_audio_file.unlink(missing_ok=True)

        return None

    def cleanup(self) -> None:
        self.stop_recording()

        if self.sink_module_id is not None:
            self._run(
                ["pactl", "unload-module", str(self.sink_module_id)],
                timeout=8,
            )
            self.sink_module_id = None


    def dump_audio_state(self) -> Dict[str, str]:
        sinks = self._run(["pactl", "list", "short", "sinks"], timeout=6)
        sources = self._run(["pactl", "list", "short", "sources"], timeout=6)
        return {
            "sink_name": self.sink_name,
            "sink_module_id": str(self.sink_module_id) if self.sink_module_id is not None else "",
            "sinks": sinks.stdout,
            "sources": sources.stdout,
            "sink_errors": sinks.stderr,
            "source_errors": sources.stderr,
        }
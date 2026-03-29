from __future__ import annotations

import io
import math
import os
import shutil

try:
    import pwd  # type: ignore
except Exception:
    pwd = None  # type: ignore

import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Optional, Sequence, Tuple

ToneSpec = Tuple[float, float, float]  # freq_hz, duration_s, silence_after_s


class TonePlayer:
    def __init__(self, tk_root=None):
        self.tk_root = tk_root
        self._simpleaudio = None
        self._gst = None
        self._last_backend = "uninitialized"
        self._sudo_user = os.environ.get("SUDO_USER") or ""
        self._sudo_uid = os.environ.get("SUDO_UID") or ""
        self._running_under_sudo = (os.name == "posix" and os.geteuid() == 0 and bool(self._sudo_user))
        self._proxy_env = self._build_proxy_env()
        try:
            import simpleaudio as sa  # type: ignore
            self._simpleaudio = sa
            self._last_backend = "simpleaudio-ready"
        except Exception:
            self._simpleaudio = None
        try:
            import gi  # type: ignore
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst  # type: ignore
            Gst.init(None)
            self._gst = Gst
            if self._simpleaudio is None:
                self._last_backend = "gstreamer-ready"
        except Exception:
            self._gst = None
        self._last_event_ts: dict[str, float] = {}
        self._lock = threading.Lock()

    @property
    def last_backend(self) -> str:
        return self._last_backend

    @property
    def running_under_sudo(self) -> bool:
        return self._running_under_sudo

    def _build_proxy_env(self) -> Optional[dict[str, str]]:
        if not self._running_under_sudo:
            return None
        env = os.environ.copy()
        uid = (self._sudo_uid or '').strip()
        user = (self._sudo_user or '').strip()
        if uid:
            runtime_dir = f"/run/user/{uid}"
            env.setdefault("XDG_RUNTIME_DIR", runtime_dir)
            env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime_dir}/bus")
        if user and pwd is not None:
            try:
                pw = pwd.getpwnam(user)
                env["HOME"] = pw.pw_dir
                env["LOGNAME"] = user
                env["USER"] = user
                env["USERNAME"] = user
            except Exception:
                pass
        return env

    def _run_as_original_user_prefix(self) -> Optional[list[str]]:
        if not self._running_under_sudo or not self._sudo_user:
            return None
        runuser = shutil.which("runuser")
        if runuser:
            return [runuser, "-u", self._sudo_user, "--"]
        sudo = shutil.which("sudo")
        if sudo:
            return [sudo, "-u", self._sudo_user, "--"]
        return None

    def play(self, event: str) -> bool:
        # simple throttling to avoid flooding on rapid UI updates
        now = time.monotonic()
        limit = 0.0
        if event == "freq_change":
            limit = 0.06
        elif event == "command_done":
            limit = 0.12
        elif event == "file_done":
            limit = 0.2
        with self._lock:
            prev = self._last_event_ts.get(event, 0.0)
            if (now - prev) < limit:
                return False
            self._last_event_ts[event] = now
        specs = self._specs_for_event(event)
        if not specs:
            return False
        wav_bytes = self._build_wav(specs)
        self._play_wav_async(wav_bytes)
        return True

    def _specs_for_event(self, event: str) -> Sequence[ToneSpec]:
        if event == "freq_change":
            return [(740.0, 0.07, 0.0)]
        if event == "command_done":
            return [(440.0, 0.08, 0.04), (440.0, 0.08, 0.0)]
        if event == "file_done":
            return [(261.63, 0.14, 0.04), (329.63, 0.14, 0.04), (392.0, 0.18, 0.0)]
        if event == "sound_test":
            return [(523.25, 0.10, 0.04), (659.25, 0.10, 0.04), (783.99, 0.16, 0.0)]
        return []

    def _build_wav(self, specs: Sequence[ToneSpec], sample_rate: int = 44100) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            frames = bytearray()
            for freq, dur, gap in specs:
                frames.extend(self._tone_samples(freq, dur, sample_rate))
                if gap > 0.0:
                    frames.extend(b"\x00\x00" * int(sample_rate * gap))
            wf.writeframes(bytes(frames))
        return buf.getvalue()

    def _tone_samples(self, freq_hz: float, duration_s: float, sample_rate: int) -> bytes:
        total = max(1, int(sample_rate * max(0.01, duration_s)))
        fade = max(1, min(total // 8, int(sample_rate * 0.01)))
        out = bytearray()
        amp = 0.82
        two_pi = 2.0 * math.pi
        for i in range(total):
            env = 1.0
            if i < fade:
                env = i / float(fade)
            elif i >= total - fade:
                env = (total - i - 1) / float(fade)
            if env < 0.0:
                env = 0.0
            sample = math.sin(two_pi * float(freq_hz) * (i / float(sample_rate))) * amp * env
            out.extend(struct.pack('<h', int(max(-1.0, min(1.0, sample)) * 32767)))
        return bytes(out)

    def _play_wav_async(self, wav_bytes: bytes) -> None:
        t = threading.Thread(target=self._play_wav_blocking, args=(wav_bytes,), daemon=True)
        t.start()

    def _play_wav_blocking(self, wav_bytes: bytes) -> None:
        # Preferred: simpleaudio (self-contained, async-friendly).
        # Under sudo on Linux this often targets root's non-existent audio session,
        # so we intentionally skip it there and proxy through the original desktop user.
        if self._simpleaudio is not None and not self._running_under_sudo:
            try:
                wave_read = wave.open(io.BytesIO(wav_bytes), 'rb')
                obj = self._simpleaudio.WaveObject.from_wave_read(wave_read)
                obj.play()
                self._last_backend = "simpleaudio"
                return
            except Exception:
                pass

        # Windows: generated WAV via winsound memory buffer.
        if sys.platform.startswith("win"):
            try:
                import winsound
                winsound.PlaySound(wav_bytes, winsound.SND_MEMORY | winsound.SND_ASYNC)
                self._last_backend = "winsound"
                return
            except Exception:
                pass

        # POSIX fallback: write a temp WAV and try self-contained players first.
        # Some GI/GStreamer setups report success but remain silent on Ubuntu, so
        # external players are preferred before the embedded Gst path.
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(prefix="jds6600_beep_", suffix=".wav", delete=False) as tf:
                tf.write(wav_bytes)
                tmp = tf.name

            for candidate in (
                "pw-play",
                "paplay",
                "aplay",
                "ffplay",
                "gst-play-1.0",
                "gst123",
                "play",
                "mpv",
                "cvlc",
                "canberra-gtk-play",
                "afplay",
            ):
                args = self._player_args(candidate, tmp)
                if not args:
                    continue
                try:
                    proc = subprocess.run(
                        args,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5.0,
                        check=False,
                        env=self._proxy_env,
                    )
                    if proc.returncode == 0:
                        self._last_backend = f"{candidate}-via-sudo-user" if self._running_under_sudo else candidate
                        return
                except subprocess.TimeoutExpired:
                    # Most of these short WAVs finish well under a second.
                    # A timeout likely means the player got stuck.
                    continue
                except Exception:
                    continue

            if self._gst is not None and self._play_via_gstreamer(tmp):
                self._last_backend = "gstreamer-via-sudo-user" if self._running_under_sudo else "gstreamer"
                return
        except Exception:
            pass
        finally:
            if tmp:
                threading.Thread(target=self._cleanup_file_later, args=(tmp,), daemon=True).start()

        # Last fallback: Tk bell / terminal bell.
        try:
            if self.tk_root is not None:
                self.tk_root.bell()
                self._last_backend = "tk-bell"
                return
        except Exception:
            pass
        try:
            sys.stdout.write("\a")
            sys.stdout.flush()
            self._last_backend = "terminal-bell"
        except Exception:
            self._last_backend = "unavailable"

    def _player_args(self, candidate: str, tmp: str) -> Optional[list[str]]:
        path = shutil.which(candidate)
        if not path:
            return None
        if candidate == "canberra-gtk-play":
            cmd = [path, "-f", tmp]
        elif candidate == "pw-play":
            cmd = [path, tmp]
        elif candidate == "paplay":
            cmd = [path, tmp]
        elif candidate == "aplay":
            cmd = [path, "-q", tmp]
        elif candidate == "ffplay":
            cmd = [path, "-nodisp", "-autoexit", "-loglevel", "quiet", tmp]
        elif candidate == "gst-play-1.0":
            cmd = [path, "--quiet", tmp]
        elif candidate == "gst123":
            cmd = [path, "-q", tmp]
        elif candidate == "play":
            cmd = [path, "-q", tmp]
        elif candidate == "mpv":
            cmd = [path, "--no-terminal", "--really-quiet", tmp]
        elif candidate == "cvlc":
            cmd = [path, "--play-and-exit", "--intf", "dummy", tmp]
        elif candidate == "afplay":
            cmd = [path, tmp]
        else:
            return None
        prefix = self._run_as_original_user_prefix()
        if prefix and os.name == "posix":
            return prefix + cmd
        return cmd

    def _play_via_gstreamer(self, tmp: str) -> bool:
        # Embedded GI/GStreamer is only safe in the current process/session. When
        # the app is launched with sudo, the GUI belongs to the desktop user while
        # this Python process belongs to root, so in-process Gst is the wrong tool.
        if self._running_under_sudo:
            return False
        Gst = self._gst
        if Gst is None:
            return False
        try:
            uri = Path(tmp).resolve().as_uri()
            playbin = Gst.ElementFactory.make("playbin", None)
            if playbin is None:
                return False
            playbin.set_property("uri", uri)
            bus = playbin.get_bus()
            playbin.set_state(Gst.State.PLAYING)
            if bus is None:
                playbin.set_state(Gst.State.NULL)
                return False
            msg = bus.timed_pop_filtered(
                int(5000 * 1000000),
                Gst.MessageType.ERROR | Gst.MessageType.EOS,
            )
            playbin.set_state(Gst.State.NULL)
            if msg is None:
                return False
            try:
                return msg.type == Gst.MessageType.EOS
            except Exception:
                return False
        except Exception:
            return False

    def _cleanup_file_later(self, path: str) -> None:
        time.sleep(5.0)
        try:
            os.remove(path)
        except Exception:
            pass

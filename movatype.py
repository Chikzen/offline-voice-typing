# -*- coding: utf-8 -*-
"""
MovaType - live voice typing for Windows (Ukrainian, Russian, English, German).

Double-tap Ctrl (or Num Lock / Scroll Lock / Pause) toggles dictation ON/OFF.
While ON the script listens continuously, cuts speech into phrases at natural
pauses, transcribes each one and pastes it into whatever field has focus - so
text appears while you keep talking. A small always-on-top badge shows state.

https://github.com/Evronot/movatype
"""

import base64
import ctypes
import io
import os
import queue
import sys
import threading
import time
import wave
import winsound
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np
import requests
import sounddevice as sd
import keyboard
import pyperclip
import tkinter as tk

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "movatype.log"
KEY_FILE = BASE_DIR / "api_key.txt"
SETTINGS_FILE = BASE_DIR / "settings.txt"
LAST_WAV = BASE_DIR / "last_phrase.wav"

_settings = {}
if SETTINGS_FILE.exists():
    for _raw in SETTINGS_FILE.read_text(encoding="utf-8").splitlines():
        _line = _raw.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            _settings[_k.strip().upper()] = _v.strip()


def cfg(name, default):
    return _settings.get(name, os.environ.get("GD_" + name, default))


# ENGINE: "stt" = dedicated speech-to-text model (cannot invent text),
#         "chat" = Gemini via chat completions (kept as a fallback).
ENGINE = cfg("ENGINE", "stt").lower()
STT_MODEL = cfg("STT_MODEL", "openai/whisper-large-v3-turbo")
CHAT_MODEL = cfg("CHAT_MODEL", "google/gemini-2.5-flash")
# Empty LANGUAGE = auto-detect per phrase. ALLOWED_LANGS is the guard: if
# the model reports a language Alex does not dictate in, the phrase is
# transcribed again as FALLBACK_LANG instead of producing e.g. Belarusian.
LANGUAGE = cfg("LANGUAGE", "").strip()
ALLOWED_LANGS = {x.strip().lower()
                 for x in cfg("ALLOWED_LANGS", "uk,ru,en,de").split(",")
                 if x.strip()}
FALLBACK_LANG = cfg("FALLBACK_LANG", "uk").strip()
LANG_MAP = {
    "ukrainian": "uk", "russian": "ru", "english": "en", "german": "de",
    "belarusian": "be", "polish": "pl", "bulgarian": "bg",
    "serbian": "sr", "macedonian": "mk", "czech": "cs", "slovak": "sk",
    "croatian": "hr", "dutch": "nl", "kazakh": "kk", "romanian": "ro",
}
HOTKEY = cfg("HOTKEY", "num lock, scroll lock, pause")
# Keys listed here are swallowed so the focused app never sees them.
SUPPRESS = cfg("SUPPRESS", "num lock, scroll lock")
# Double-tap of this modifier toggles dictation. Empty = disabled.
DOUBLE_TAP = cfg("DOUBLE_TAP", "ctrl").strip().lower()
TAP_GAP = float(cfg("TAP_GAP", "0.45"))
TAP_MAX_HOLD = float(cfg("TAP_MAX_HOLD", "0.40"))
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
STT_URL = "https://openrouter.ai/api/v1/audio/transcriptions"
# Provider preference. The 12-22 s outliers in the log came from routing to a
# cold provider; Groq serves whisper fastest, DeepInfra stays as fallback.
PROVIDER_ORDER = [p.strip() for p in cfg("PROVIDER_ORDER", "Groq,DeepInfra")
                  .split(",") if p.strip()]
# If a request has not answered within this many seconds, fire a duplicate in
# parallel and use whichever comes back first. Kills the long tail.
HEDGE_AFTER = float(cfg("HEDGE_AFTER", "2.5"))
LANG_HOTKEY = cfg("LANG_HOTKEY", "ctrl+alt+l")
LANG_CYCLE = [x.strip() for x in
              cfg("LANG_CYCLE", ",uk,ru,de,en").split(",")]

SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK = 512
BLOCK_SEC = BLOCK / float(SAMPLE_RATE)

SILENCE_TAIL = float(cfg("SILENCE_TAIL", "0.45"))
# How many phrases may be transcribed at the same time. Output order is
# always preserved - this only overlaps the network waits.
MAX_PARALLEL = int(cfg("MAX_PARALLEL", "3"))
PASTE_DELAY = float(cfg("PASTE_DELAY", "0.05"))
MIN_PHRASE = float(cfg("MIN_PHRASE", "0.7"))
MAX_PHRASE = 25.0
PRE_ROLL_SEC = 0.30
# Real speech on this mic peaks at 0.10-0.18; noise that produced the
# "Дякую" hallucinations peaked at 0.009-0.05. Gate well above the noise.
MIN_PEAK = float(cfg("MIN_PEAK", "0.030"))
MIN_VOICED = float(cfg("MIN_VOICED", "0.45"))
ABS_FLOOR = float(cfg("ABS_FLOOR", "0.004"))
SNR_FACTOR = float(cfg("SNR_FACTOR", "3.5"))
TARGET_PEAK = 0.5
MAX_GAIN = float(cfg("MAX_GAIN", "12.0"))

# Whisper-family models emit these on silence/noise instead of nothing.
FILLERS = {
    "дякую", "дякую!", "дякую.", "дякуємо", "спасибо", "спасибо.",
    "спасибо!", "спасибо за просмотр", "спасибо за внимание",
    "thank you", "thank you.", "thanks", "thanks for watching",
    "so", "so.", "you", "bye", "bye.", "продовження далі", "субтитри",
    "субтитры", "редактор субтитров", "так", "так.", "да", "да.",
    "ага", "угу", "the end", "аминь", "amen",
}

TRANSCRIBE_PROMPT = (
    "Transcribe the audio verbatim, in the same language it is spoken. "
    "Never translate. Never continue, complete, summarise or answer what "
    "the speaker says. Output only words that are actually audible in the "
    "recording - if you are unsure, output nothing. Add natural punctuation. "
    "No labels, no comments, no quotation marks."
)

_active = False
_stream = None
_pasting = threading.Event()
_state_lock = threading.Lock()
_audio_q = queue.Queue()
_tx_q = queue.Queue()
_out_q = queue.Queue()
_last_lang = ["?"]
_forced_lang = [LANGUAGE]
_ui = None


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
    try:
        print(line)
    except Exception:
        pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def beep(kind):
    try:
        if kind == "on":
            winsound.Beep(1000, 90)
            winsound.Beep(1400, 90)
        elif kind == "off":
            winsound.Beep(700, 90)
            winsound.Beep(450, 90)
        elif kind == "error":
            winsound.Beep(300, 400)
    except Exception:
        pass


class Badge:
    """Small always-on-top status window that never takes focus."""

    COLORS = {
        "off": ("#3a3a3a", "#bdbdbd"),
        "idle": ("#1f5130", "#9ae6b4"),
        "speech": ("#7a1f1f", "#ffd0d0"),
        "work": ("#7a5a1f", "#ffe9b0"),
        "error": ("#7a1f1f", "#ffd0d0"),
    }

    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.win = tk.Toplevel(self.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.92)
        self.label = tk.Label(
            self.win, text="", font=("Segoe UI", 11, "bold"),
            padx=14, pady=7, bg="#3a3a3a", fg="#bdbdbd",
        )
        self.label.pack()
        self.win.update_idletasks()
        self._no_activate()
        self.win.withdraw()

    def _no_activate(self):
        """WS_EX_NOACTIVATE - the badge must never steal keyboard focus."""
        try:
            hwnd = self.win.winfo_id()
            parent = ctypes.windll.user32.GetParent(hwnd)
            if parent:
                hwnd = parent
            GWL_EXSTYLE = -20
            WS_EX_NOACTIVATE = 0x08000000
            WS_EX_TOOLWINDOW = 0x00000080
            user32 = ctypes.windll.user32
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE,
                style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        except Exception as exc:
            log("WARNING no_activate: %s" % exc)

    def _place(self):
        self.win.update_idletasks()
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        w = self.win.winfo_width()
        h = self.win.winfo_height()
        self.win.geometry("+%d+%d" % (sw - w - 24, sh - h - 90))

    def _apply(self, state, text, visible):
        try:
            bg, fg = self.COLORS.get(state, self.COLORS["idle"])
            self.label.configure(text=text, bg=bg, fg=fg)
            self.win.configure(bg=bg)
            if visible:
                self.win.deiconify()
                self.win.attributes("-topmost", True)
                self._place()
            else:
                self.win.withdraw()
        except Exception:
            pass

    def set(self, state, text, visible=True):
        try:
            self.root.after(0, self._apply, state, text, visible)
        except Exception:
            pass

    def hide(self):
        self.set("off", "", False)

    def run(self):
        self.root.mainloop()


def ui(state, text, visible=True):
    if _ui is not None:
        _ui.set(state, text, visible)


def get_api_key():
    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if key and not key.startswith("PASTE"):
        return key
    if KEY_FILE.exists():
        for raw in KEY_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                return line
    return ""


def wav_bytes(audio):
    audio_int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio_int16.tobytes())
    return buf.getvalue()


def _headers():
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError("no OpenRouter API key")
    return {"Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "X-Title": "MovaType"}


def _post(url, payload):
    resp = requests.post(url, headers=_headers(), json=payload, timeout=90)
    if resp.status_code >= 400:
        raise RuntimeError("HTTP %s: %s" % (resp.status_code, resp.text[:300]))
    return resp.json()


def _post_hedged(url, payload, alt_payload=None):
    """Send the request; if it is still silent after HEDGE_AFTER seconds,
    send a second one in parallel - routed to the OTHER provider - and use
    whichever answers first. A duplicate costs a fraction of a cent; a 20
    second stall costs the whole point of dictating."""
    if HEDGE_AFTER <= 0:
        return _post(url, payload)
    done = threading.Event()
    box = {}
    state = {"launched": 1, "failed": 0}
    lock = threading.Lock()

    def attempt(tag):
        try:
            body = alt_payload if (tag == "hedge" and alt_payload) else payload
            data = _post(url, body)
            with lock:
                if "data" not in box:
                    box["data"] = data
                    box["tag"] = tag
            done.set()
        except Exception as exc:
            with lock:
                box.setdefault("exc", exc)
                state["failed"] += 1
                if state["failed"] >= state["launched"]:
                    done.set()

    threading.Thread(target=attempt, args=("first",), daemon=True).start()
    if not done.wait(HEDGE_AFTER):
        with lock:
            state["launched"] += 1
        log("slow provider (>%.1fs) - sending a parallel duplicate" % HEDGE_AFTER)
        threading.Thread(target=attempt, args=("hedge",), daemon=True).start()
    if not done.wait(95):
        raise RuntimeError("no answer from the API")
    if "data" in box:
        return box["data"]
    raise box.get("exc") or RuntimeError("request failed")


def _norm_lang(value):
    v = (value or "").strip().lower()
    return LANG_MAP.get(v, v)


def transcribe_stt(data):
    """Dedicated ASR model - structurally unable to invent or translate.

    With LANGUAGE empty the model detects the language of each phrase on its
    own, so Russian stays Russian and Ukrainian stays Ukrainian. If it lands
    on a language Alex never dictates in, the phrase is redone once as
    FALLBACK_LANG."""
    forced = _forced_lang[0]
    payload = {
        "model": STT_MODEL,
        "input_audio": {"data": base64.b64encode(data).decode("utf-8"),
                        "format": "wav"},
        "temperature": 0,
        "response_format": "verbose_json",
    }
    if PROVIDER_ORDER:
        payload["provider"] = {"order": PROVIDER_ORDER,
                               "allow_fallbacks": True}
    if forced:
        payload["language"] = forced
    alt = None
    if len(PROVIDER_ORDER) > 1:
        alt = dict(payload)
        alt["provider"] = {"order": list(reversed(PROVIDER_ORDER)),
                           "allow_fallbacks": True}
    result = _post_hedged(STT_URL, payload, alt)
    text = (result.get("text") or "").strip()
    detected = _norm_lang(result.get("language"))
    if not forced and ALLOWED_LANGS and detected \
            and detected not in ALLOWED_LANGS:
        log("language %r is outside %s - redoing as %s"
            % (detected, sorted(ALLOWED_LANGS), FALLBACK_LANG))
        payload["language"] = FALLBACK_LANG
        result = _post_hedged(STT_URL, payload, alt)
        text = (result.get("text") or "").strip()
        detected = FALLBACK_LANG
    _last_lang[0] = detected or "?"
    return text


def transcribe_chat(data):
    payload = {
        "model": CHAT_MODEL,
        "temperature": 0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": TRANSCRIBE_PROMPT},
                {"type": "input_audio", "input_audio": {
                    "data": base64.b64encode(data).decode("utf-8"),
                    "format": "wav"}},
            ],
        }],
    }
    return (_post(CHAT_URL, payload)["choices"][0]["message"]["content"]
            or "").strip()


def transcribe(data):
    try:
        LAST_WAV.write_bytes(data)
    except Exception:
        pass
    if ENGINE == "chat":
        return transcribe_chat(data)
    return transcribe_stt(data)


def paste(text):
    _pasting.set()
    try:
        try:
            old = pyperclip.paste()
        except Exception:
            old = None
        for mod in ("ctrl", "alt", "shift", "windows"):
            try:
                keyboard.release(mod)
            except Exception:
                pass
        pyperclip.copy(text)
        time.sleep(PASTE_DELAY)
        keyboard.send("ctrl+v")
        time.sleep(PASTE_DELAY * 2)
        if old is not None:
            try:
                pyperclip.copy(old)
            except Exception:
                pass
    finally:
        time.sleep(PASTE_DELAY)
        _pasting.clear()


def is_filler(text, dur, peak):
    """A lone stock phrase on a short/quiet segment is a hallucination,
    not speech. Real deliberate 'Дякую' is louder and rarely alone."""
    clean = text.strip().strip('"«»').lower()
    if clean not in FILLERS:
        return False
    return dur < 2.0 or peak < 0.07


def archive(data, text):
    """Keep the last phrases as WAV so models can be compared on real voice."""
    try:
        d = BASE_DIR / "phrases"
        d.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%H%M%S")
        (d / ("%s.wav" % stamp)).write_bytes(data)
        with open(d / "index.txt", "a", encoding="utf-8") as fh:
            fh.write("%s.wav\t%s\n" % (stamp, text))
        old = sorted(d.glob("*.wav"))[:-40]
        for f in old:
            f.unlink(missing_ok=True)
    except Exception:
        pass


def audio_callback(indata, frames, time_info, status):
    if _active:
        _audio_q.put(indata[:, 0].copy())


def _do_transcribe(item):
    audio, dur, peak, voiced_sec = item
    gain = min(TARGET_PEAK / peak, MAX_GAIN) if peak > 0 else 1.0
    loud = np.clip(audio * gain, -1.0, 1.0)
    data = wav_bytes(loud)
    t0 = time.time()
    text = transcribe(data)
    log("phrase %.1fs peak=%.4f gain=x%.1f api=%.1fs lang=%s -> %s"
        % (dur, peak, gain, time.time() - t0, _last_lang[0],
           text or "(empty)"))
    if text and is_filler(text, dur, peak):
        log("drop: filler hallucination %r (%.1fs peak=%.4f)"
            % (text, dur, peak))
        return None, None
    return text, data


def dispatcher_worker():
    """Sends phrases to the API in parallel - network waits overlap."""
    pool = ThreadPoolExecutor(max_workers=MAX_PARALLEL,
                              thread_name_prefix="stt")
    while True:
        item = _tx_q.get()
        if item is None:
            _out_q.put(None)
            continue
        ui("work", "... розпізнаю")
        _out_q.put(pool.submit(_do_transcribe, item))


def paster_worker():
    """Pastes results strictly in the order the phrases were spoken."""
    first = True
    while True:
        fut = _out_q.get()
        if fut is None:
            first = True
            continue
        try:
            text, data = fut.result()
            if text:
                archive(data, text)
                paste(text if first else " " + text)
                first = False
        except Exception as exc:
            log("ERROR transcribe: %s" % exc)
            ui("error", "помилка API")
            beep("error")
            time.sleep(1.0)
        finally:
            if _active and _out_q.empty():
                ui("idle", "* диктування")


def segmenter_worker():
    """Cuts the incoming stream into phrases at natural pauses."""
    noise = deque(maxlen=int(3.0 / BLOCK_SEC))
    preroll = deque(maxlen=max(1, int(PRE_ROLL_SEC / BLOCK_SEC)))
    phrase = []
    voiced = 0
    voiced_sec = 0.0
    silence = 0.0
    speaking = False

    def flush():
        nonlocal phrase, speaking, silence, voiced, voiced_sec
        if phrase:
            audio = np.concatenate(phrase)
            dur = len(audio) / float(SAMPLE_RATE)
            peak = float(np.abs(audio).max()) if audio.size else 0.0
            if dur < MIN_PHRASE:
                log("drop: too short %.2fs peak=%.4f" % (dur, peak))
            elif peak < MIN_PEAK:
                log("drop: too quiet %.2fs peak=%.4f (noise, not speech)"
                    % (dur, peak))
            elif voiced_sec < MIN_VOICED:
                log("drop: only %.2fs voiced in %.2fs peak=%.4f"
                    % (voiced_sec, dur, peak))
            else:
                _tx_q.put((audio, dur, peak, voiced_sec))
        phrase = []
        speaking = False
        silence = 0.0
        voiced = 0
        voiced_sec = 0.0

    while True:
        block = _audio_q.get()
        if block is None:
            flush()
            noise.clear()
            preroll.clear()
            continue
        rms = float(np.sqrt(np.mean(block ** 2)))
        noise.append(rms)
        floor = float(np.percentile(noise, 20)) if len(noise) > 15 else rms
        thr = max(ABS_FLOOR, floor * SNR_FACTOR)
        if not speaking:
            preroll.append(block)
            if rms > thr:
                voiced += 1
                if voiced >= 2:
                    speaking = True
                    phrase = list(preroll)
                    preroll.clear()
                    silence = 0.0
                    ui("speech", "* говорите")
            else:
                voiced = 0
        else:
            phrase.append(block)
            if rms > thr:
                silence = 0.0
                voiced_sec += BLOCK_SEC
            else:
                silence += BLOCK_SEC
            dur = len(phrase) * BLOCK_SEC
            if silence >= SILENCE_TAIL or dur >= MAX_PHRASE:
                flush()
                if _active:
                    ui("work", "... розпізнаю")


def start_dictation():
    global _active, _stream
    with _state_lock:
        if _active:
            return
        try:
            _stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                                     blocksize=BLOCK, callback=audio_callback)
            _stream.start()
        except Exception as exc:
            log("ERROR microphone: %s" % exc)
            ui("error", "нет микрофона")
            beep("error")
            _stream = None
            return
        _active = True
    log("dictation ON")
    beep("on")
    ui("idle", "* диктування увімкнено")


def stop_dictation():
    global _active, _stream
    with _state_lock:
        if not _active:
            return
        _active = False
        try:
            _stream.stop()
            _stream.close()
        except Exception:
            pass
        _stream = None
    _audio_q.put(None)
    _tx_q.put(None)
    log("dictation OFF")
    beep("off")
    ui("off", "диктування вимкнено")
    threading.Timer(1.6, lambda: _ui and _ui.hide()).start()


# Num Lock / Scroll Lock also flip a Windows state we do not want to change.
# Blocking them with suppress=True breaks the whole keyboard listener, so
# instead we let the flip happen and immediately flip it back.
LOCK_KEYS = {"num lock": 0x90, "scroll lock": 0x91, "caps lock": 0x14}
_lock_guard = [0.0]


def _flip_lock(vk):
    try:
        user32 = ctypes.windll.user32
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, 2, 0)
    except Exception as exc:
        log("WARNING restore lock state: %s" % exc)


def make_toggle(combo):
    vk = LOCK_KEYS.get(combo.strip().lower())
    if vk is None:
        return toggle

    def cb():
        now = time.time()
        if now < _lock_guard[0]:
            return
        _lock_guard[0] = now + 0.8
        threading.Timer(0.05, _flip_lock, args=(vk,)).start()
        toggle()

    return cb


def cycle_language():
    """Force one language for a while - useful for German, which is hard to
    auto-detect inside short Ukrainian/Russian sentences."""
    try:
        cur = _forced_lang[0]
        idx = LANG_CYCLE.index(cur) if cur in LANG_CYCLE else 0
        nxt = LANG_CYCLE[(idx + 1) % len(LANG_CYCLE)]
        _forced_lang[0] = nxt
        label = nxt.upper() if nxt else "АВТО"
        log("forced language -> %s" % (nxt or "auto"))
        ui("work", "мова: %s" % label)
        threading.Timer(
            1.8, lambda: ui("idle", "* диктування") if _active
            else (_ui and _ui.hide())).start()
        beep("ok")
    except Exception as exc:
        log("WARNING cycle_language: %s" % exc)


_tap = {"last": 0.0, "down": 0.0, "clean": False}


def install_double_tap(key):
    """Toggle on a double tap of a modifier (default Ctrl).

    A tap counts only when the key went down and up alone: if any other key
    was pressed while it was held, it was a shortcut (Ctrl+C, Ctrl+V) and is
    ignored. Our own synthetic Ctrl+V during pasting is ignored too."""
    names = {"ctrl": ("ctrl", "left ctrl", "right ctrl"),
             "shift": ("shift", "left shift", "right shift"),
             "alt": ("alt", "left alt", "right alt", "alt gr")}
    watched = names.get(key, (key,))

    def handler(event):
        try:
            _handle(event)
        except Exception as exc:
            log("WARNING double-tap handler: %s" % exc)

    def _handle(event):
        if _pasting.is_set():
            return
        name = (event.name or "").lower()
        hit = name in watched
        if event.event_type == "down":
            if hit:
                if _tap["down"] == 0.0:
                    _tap["down"] = time.time()
                    _tap["clean"] = True
            else:
                _tap["clean"] = False
                _tap["last"] = 0.0
        elif event.event_type == "up" and hit:
            held = time.time() - _tap["down"] if _tap["down"] else 99.0
            clean = _tap["clean"]
            _tap["down"] = 0.0
            _tap["clean"] = False
            if not clean or held > TAP_MAX_HOLD:
                _tap["last"] = 0.0
                return
            now = time.time()
            if 0.0 < now - _tap["last"] < TAP_GAP:
                _tap["last"] = 0.0
                toggle()
            else:
                _tap["last"] = now

    keyboard.hook(handler)


def toggle():
    if _active:
        threading.Thread(target=stop_dictation, daemon=True).start()
    else:
        threading.Thread(target=start_dictation, daemon=True).start()


def main():
    global _ui
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    log("=" * 50)
    log("MovaType starting, engine=%s model=%s lang=%s"
        % (ENGINE, STT_MODEL if ENGINE != "chat" else CHAT_MODEL,
           LANGUAGE or "auto"))
    if not get_api_key():
        log("ERROR: no API key in %s" % KEY_FILE)
        beep("error")
        sys.exit(1)
    try:
        log("input device: %s" % sd.query_devices(kind="input")["name"])
    except Exception as exc:
        log("WARNING input device: %s" % exc)

    threading.Thread(target=segmenter_worker, daemon=True).start()
    threading.Thread(target=dispatcher_worker, daemon=True).start()
    threading.Thread(target=paster_worker, daemon=True).start()

    suppressed = {k.strip().lower() for k in SUPPRESS.split(",") if k.strip()}
    registered = []
    for combo in [c.strip() for c in HOTKEY.split(",") if c.strip()]:
        try:
            keyboard.add_hotkey(combo, make_toggle(combo),
                                suppress=combo.lower() in suppressed)
            registered.append(combo)
        except Exception as exc:
            log("WARNING hotkey %r not registered: %s" % (combo, exc))
    if not registered:
        log("ERROR: no hotkey could be registered")
        beep("error")
        sys.exit(1)
    log("hotkeys: %s" % ", ".join(registered))
    if LANG_HOTKEY:
        try:
            keyboard.add_hotkey(LANG_HOTKEY, cycle_language)
            log("language switch: %s (cycle: %s)"
                % (LANG_HOTKEY, " -> ".join(x or "auto" for x in LANG_CYCLE)))
        except Exception as exc:
            log("WARNING language hotkey: %s" % exc)
    if DOUBLE_TAP:
        try:
            install_double_tap(DOUBLE_TAP)
            log("double tap: %s" % DOUBLE_TAP)
        except Exception as exc:
            log("WARNING double tap %r failed: %s" % (DOUBLE_TAP, exc))

    _ui = Badge()
    log("ready")
    ui("off", "MovaType: %s" % registered[0])
    threading.Timer(3.0, lambda: _ui and _ui.hide()).start()
    _ui.run()


if __name__ == "__main__":
    main()

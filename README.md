# Ukrainian Voice Typing for Windows

**Global voice typing for Windows with Ukrainian speech recognition** — plus
Russian, English and German. Press a key, talk, and the text lands in whatever
field has focus: browser, Word, Excel, Telegram, your CRM.

Speech-to-text · dictation · voice input · Ukrainian · українська мова

Built because Windows' own voice typing (`Win+H`) **does not support
Ukrainian**. Russian, Polish and Bulgarian are there. Ukrainian is not, and
[the request to add it](https://learn.microsoft.com/en-us/answers/questions/5913553/add-ukrainian-language-support-for-windows-voice-t)
has no answer from Microsoft.

Roughly 300 lines of Python, no installer, no telemetry, no account.

---

## How it works

1. While dictation is on, the microphone is recorded continuously.
2. Speech is cut into phrases at natural pauses — voice activity detection
   with an adaptive noise floor.
3. Each phrase goes to a speech-to-text model, up to 3 requests in parallel.
4. Text is pasted **strictly in the order it was spoken**.

Text appears phrase by phrase *while you keep talking*, not after you stop.
Typical delay after a pause is 1–2 seconds.

## Controls

| Action | Key |
|---|---|
| Start / stop dictation | **double-tap Ctrl** |
| Same | **Num Lock**, **Scroll Lock**, **Pause** |
| Cycle language: auto → uk → ru → de → en | **Ctrl+Alt+L** |

Double-tapping Ctrl only counts when Ctrl is pressed and released *alone* —
`Ctrl+C` and `Ctrl+V` never trigger it. Num Lock and Scroll Lock are restored
to their previous state right after firing, so the numeric keypad and Excel
arrow keys keep working.

Audio feedback: two rising beeps = on, two falling = off, one long low =
error. A small badge in the corner shows the current state.

## Install

Requires Python 3.10+.

```
git clone https://github.com/alex80674097219/ukrainian-voice-typing
cd ukrainian-voice-typing
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
```

Get an API key at [openrouter.ai/keys](https://openrouter.ai/keys), then:

```
copy api_key.example.txt api_key.txt
```

and paste your key into `api_key.txt` (it is in `.gitignore`).

Run it:

```
venv\Scripts\pythonw.exe dictate.py
```

To start it with Windows, put a shortcut to that command in `shell:startup`.

## Configuration

Everything lives in `settings.txt`. Restart after editing.

| Setting | What it does |
|---|---|
| `STT_MODEL` | Recognition model |
| `LANGUAGE` | Empty = detect language per phrase |
| `ALLOWED_LANGS` | If a language outside this list is detected, the phrase is redone |
| `SILENCE_TAIL` | Pause length that ends a phrase |
| `MIN_PEAK`, `MIN_VOICED` | Noise gate — see "hallucinations" below |
| `PROVIDER_ORDER`, `HEDGE_AFTER` | Latency control |
| `MAX_PARALLEL` | Concurrent recognition requests |

## What this cost me to learn

Measurements below are from real dictation on one setup — a USB desktop mic,
Ukrainian and Russian speech with solar-industry terms. Your numbers will
differ, but the *shape* of the problems generalises.

### Chat models invent text. Use a real ASR model.

The first version sent audio to a chat model (Gemini) with a prompt asking it
to transcribe. It produced an entire fluent English monologue that was never
spoken — assembled from vocabulary hints in my own prompt. A chat model
answers; it does not transcribe. Switching to a dedicated speech-to-text
endpoint made that class of failure structurally impossible.

### Whisper says "thank you" to silence

On silence, breath or a mouse click, Whisper-family models do not return an
empty string. They return the most common phrase in their training data —
"Дякую", "Спасибо", "Thank you", "Субтитры". Measured on my mic:

| | Peak amplitude | Duration |
|---|---|---|
| Real speech | 0.10 – 0.18 | 2.8 – 6.5 s |
| Hallucinated "Дякую" | 0.009 – 0.05 | 0.8 – 1.0 s |

Three defences, all in `settings.txt`: a peak-amplitude gate, a minimum amount
of voiced time inside a phrase, and a blocklist of stock phrases that is only
applied to short or quiet segments.

Related trap: auto-gain. Normalising a quiet recording by 30x turns room noise
into something that *sounds* like speech to the model. Gain is capped at 12x.

### Forcing a language breaks multilingual dictation

Setting `language=uk` fixed one bug and created a worse one — Russian speech
came back rendered as Ukrainian. Auto-detection was measured against forced
Ukrainian across 14 recordings: **identical output**, no drift. Leave
`LANGUAGE` empty and use `ALLOWED_LANGS` as a guard instead: neighbouring
Slavic languages get misdetected, and that phrase is simply redone.

Short German words inside a Ukrainian sentence still defeat auto-detection —
hence the manual language key.

### The slowness was never the model

Measured API response times inside one session:

```
0.8 · 0.9 · 1.3 · 1.7 · 12.3 · 21.8 · 3.2 · 2.7 · 2.0 · 12.3 · 13.2
```

Median around 2 s with regular 12–22 s stalls — cold starts on the routing
provider, not recognition. Two fixes: pin the provider order, and if a request
is still silent after `HEDGE_AFTER` seconds, send a duplicate to the *other*
provider and take whichever answers first. A duplicate request costs a
fraction of a cent; a 20-second stall costs the point of dictating.

### Model comparison (same 5 recordings, median of 5 runs)

| Model | Latency | Brand names | Numbers |
|---|---|---|---|
| `openai/whisper-large-v3-turbo` | 1.34 s | wrong ("Pylon Edge") | correct ("20 кВт") |
| `openai/gpt-4o-transcribe` | 1.21 s | correct | spelled out as words |
| `google/chirp-3` | 2.82 s | correct | correct |
| `openai/gpt-4o-mini-transcribe` | 1.00 s | correct | drifted into Belarusian |
| `deepgram/nova-3` | 1.44 s | wrong ("Pilentage") | spelled out |

Provider latency for the same model: DeepInfra median 2.26 s / max 3.25 s;
Groq median 4.70 s / max 81.8 s.

### Running it locally is not automatically faster

`faster-whisper` on CPU, 16 threads, int8, no CUDA (AMD RX 560):

| Model | Latency | Quality |
|---|---|---|
| `large-v3-turbo` | 8.5 s | excellent |
| `small` | 2.0 s | poor — languages mixed inside one sentence |

With an NVIDIA GPU local inference would win. On CPU it does not.

## Privacy

Audio of each phrase is sent to OpenRouter for recognition and the text comes
back. Nothing is sent anywhere else, there is no telemetry and no account.
Recognised phrases are kept locally in `phrases/` (last 40) so you can compare
models on your own voice — delete that folder or the `archive()` call if you
do not want it.

Your clipboard is used to paste text and is restored immediately afterwards.

## A note on antivirus

This program registers a global keyboard hook, reads the clipboard and records
the microphone. That is, technically, the exact signature of a keylogger.
Windows Defender or your antivirus may flag it. The source is ~300 lines in
one file — read it before trusting it, which is the honest answer for any tool
that can do these things.

## Limitations

- Not word-by-word streaming. Phrases arrive after a pause, 1–2 s behind.
  True real-time needs a streaming WebSocket provider — different architecture.
- Won't see the hotkey while an elevated (admin) window has focus. Windows
  restriction; run it elevated too if you need that.
- Latin brand names inside Slavic speech are hit-and-miss on every model tested.

## License

MIT — see [LICENSE](LICENSE).

---

## Українською

Глобальний голосовий ввід для Windows з підтримкою **української** мови,
а також російської, англійської та німецької. Текст вставляється в будь-яке
активне поле.

Зроблено тому, що вбудований голосовий ввід Windows (`Win+H`) української
не підтримує — російська, польська, болгарська є, української немає.

Вмикається подвійним натисканням **Ctrl** або клавішею **Num Lock**.
Мова визначається автоматично для кожної фрази; **Ctrl+Alt+L** перемикає
примусову мову, якщо потрібно надиктувати німецькою.

Потрібен ключ [OpenRouter](https://openrouter.ai/keys). Година безперервної
диктовки коштує близько 4 центів. Усі налаштування — у `settings.txt`.

# Ukrainian Voice Typing for Windows

**Global voice typing for Windows with Ukrainian speech recognition** — plus
Russian, English and German. Press a key, talk, and the text lands in whatever
field has focus: browser, Word, Excel, Telegram, your CRM.

Speech-to-text · dictation · voice input · Ukrainian · українська мова

![Ukrainian voice typing in action](docs/demo.gif)

**Double-tap Ctrl. Talk. The text appears.** That is the whole interaction —
no window to open, no button to click, no app to switch to.

*Unedited recording: Ukrainian speech, a Latin brand name and a number, then a
switch of language mid-session — all detected automatically.*

Built because Windows' own voice typing (`Win+H`) **does not support
Ukrainian**. Russian, Polish and Bulgarian are there. Ukrainian is not, and
[the request to add it](https://learn.microsoft.com/en-us/answers/questions/5913553/add-ukrainian-language-support-for-windows-voice-t)
has no answer from Microsoft.

Roughly 300 lines of Python, no installer, no telemetry, no account. You bring
your own API key, so you pay cents for what you actually dictate instead of a
monthly subscription — about **$0.04 per hour** of continuous speech.

---

## Your language isn't Ukrainian? It still works.

Nothing here is Ukrainian-specific. The recognition model handles **~99
languages**; Windows voice typing covers about 36. If yours is in the gap,
this fills it — change one line in `settings.txt`:

```
ALLOWED_LANGS=el,en          # Greek, for example
FALLBACK_LANG=el
```

Languages Windows voice typing does **not** support, which work here:

**Ukrainian** · Greek · Hebrew · Arabic · Persian · Serbian · Bosnian ·
Macedonian · Belarusian · Georgian · Armenian · Azerbaijani · Kazakh ·
Indonesian · Malay · Bengali · Urdu · Catalan · Icelandic · Swahili · and more

Leave `LANGUAGE` empty and the language is detected per phrase, so you can
switch mid-sentence — dictate Ukrainian, drop in an English brand name,
answer a colleague in German. `ALLOWED_LANGS` is just a guard that catches
misdetection into a language you never speak.

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

**A pause to think is not a full stop.** Cut a phrase at a 0.45 s pause and
the model ends it with a period — so hesitating mid-sentence used to chop your
thought in two. The phrase is now sent for recognition immediately, but the
punctuation decision waits until `JOIN_WINDOW` (1.1 s). Speak again inside that
window and the period is dropped and the next word lowercased; stay silent and
the sentence really did end. Recognition runs during the wait, so this costs no
extra latency.

**It switches itself off.** After `IDLE_OFF` seconds of silence (2 minutes by
default) dictation stops on its own, with the badge fading out and counting
down for the last 10 seconds. A microphone left on is a liability: step away
mid-task, start talking to someone, and the room ends up pasted into whatever
chat you had open.

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
| `JOIN_WINDOW` | Below this, a pause is a hesitation, not a full stop |
| `IDLE_OFF`, `IDLE_WARN` | Switch off after silence, and the warning before it |
| `MIN_PEAK`, `MIN_VOICED` | Noise gate — see "hallucinations" below |
| `PROVIDER_ORDER`, `HEDGE_AFTER` | Latency control |
| `MAX_PARALLEL` | Concurrent recognition requests |

`replacements.txt` is a plain word list applied to the recognised text
locally — no extra request, no added latency. Recognition models are
unstable on proper nouns: the same brand comes back in Latin one day and
transliterated into Cyrillic the next. One line per term fixes it:

```
віктрон = Victron
пайлонтех = Pylontech
мппт = MPPT
```

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

### Reusing one HTTPS connection cut latency by more than half

Every phrase is a separate HTTPS request. Creating a new connection each
time means a DNS lookup, a TCP handshake and a TLS handshake before a
single byte of audio moves. Measured on 6 real phrases, same model, same
provider:

| | Median round trip |
|---|---|
| New connection per phrase | 1.34 s |
| One reused session | **0.55 s** |

0.8 seconds per phrase, paid on every phrase, for nothing. A
`requests.Session` with a mounted adapter is the entire fix and it beat
every model swap, provider change and prompt tweak in this project
combined.

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

### Голосовий ввід українською для Windows

Натискаєш двічі **Ctrl**, говориш — і текст з'являється там, де стоїть
курсор. У браузері, у Word, в Excel, у Telegram, у CRM. Нічого відкривати
й ні на що перемикатися не треба.

Зроблено тому, що вбудований голосовий ввід Windows (`Win+H`) **не має
української мови**. Російська є, польська є, болгарська є. Української
немає, і запит до Microsoft лишається без відповіді.

### Як користуватися

| Дія | Клавіша |
|---|---|
| Увімкнути / вимкнути диктування | **подвійний Ctrl** |
| Те саме | **Num Lock**, **Scroll Lock**, **Pause** |
| Змінити мову: авто → uk → ru → de → en | **Ctrl+Alt+L** |

Подвійний Ctrl рахується лише тоді, коли Ctrl натиснули й відпустили
**окремо**, тому `Ctrl+C` і `Ctrl+V` диктування не вмикають. Num Lock і
Scroll Lock одразу повертаються у попередній стан, тож цифровий блок
і стрілки в Excel працюють як завжди.

Звукові сигнали: два висхідні — увімкнено, два низхідні — вимкнено,
один довгий низький — помилка. Стан показує невелика плашка в кутку.

### Що воно вміє, крім самого розпізнавання

**Пауза на подумати — це не крапка.** Якщо різати фразу на паузі 0.45 с,
модель ставить у кінці крапку, і одна думка розривається на два речення.
Тепер фраза йде на розпізнавання одразу, але рішення про крапку чекає
до 1.1 секунди. Заговорив далі — крапка зникає, наступне слово стає
з малої літери. Промовчав довше — речення справді закінчилося. Затримка
при цьому не зростає, бо розпізнавання встигає за час очікування.

**Вимикається саме.** Після 2 хвилин тиші диктування зупиняється, а за
10 секунд до того плашка починає гаснути й показує зворотний відлік.
Увімкнений мікрофон — це ризик: відійшов, з кимось заговорив, і чужа
розмова опинилася у відкритому чаті.

**Словник термінів.** Моделі нестабільні на власних назвах: сьогодні
пишуть «Victron», завтра «Віктрон». Файл `replacements.txt` виправляє
це локально, миттєво, без жодного запиту. Один рядок на термін.

**Мова визначається для кожної фрази окремо.** Можна почати українською,
вставити англійську назву, відповісти колезі німецькою — і все запишеться
правильно. Список `ALLOWED_LANGS` ловить помилки визначення: якщо модель
вирішила, що це білоруська чи польська, фраза перепитується.

### Встановлення

Потрібен Python 3.10 або новіший.

```
git clone https://github.com/alex80674097219/ukrainian-voice-typing
cd ukrainian-voice-typing
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
copy api_key.example.txt api_key.txt
```

Ключ береться на [openrouter.ai/keys](https://openrouter.ai/keys) і
вставляється в `api_key.txt` — цей файл у `.gitignore` і в репозиторій
не потрапляє.

Запуск: `venv\Scripts\pythonw.exe dictate.py`. Щоб стартувало разом
із Windows, поклади ярлик на цю команду в теку `shell:startup`.

### Чесно про важливе

**Гроші.** Програма безкоштовна й відкрита, але розпізнавання працює
через хмарний сервіс і потребує власного ключа. Виходить близько
**4 центів за годину** безперервної диктовки. Жодних підписок — платиш
лише за те, що наговорив.

**Приватність.** Аудіо кожної фрази вирушає до OpenRouter і повертається
текстом. Більше нікуди нічого не йде: ні телеметрії, ні акаунтів. Останні
40 фраз зберігаються локально в теці `phrases/`, щоб можна було порівнювати
моделі на власному голосі — цю теку можна просто видалити.

**Антивірус.** Програма ставить глобальний перехоплювач клавіатури, читає
буфер обміну й пише з мікрофона. Технічно це точнісінько сигнатура
клавіатурного шпигуна, і Windows Defender може її позначити. Тут близько
300 рядків в одному файлі — прочитай перед тим, як довіряти. Це чесна
відповідь для будь-якої програми, здатної на такі речі.

**Чого воно не вміє.** Це не потокове розпізнавання: текст з'являється
фразами після паузи, а не окремими словами під час мовлення. Гарячу
клавішу не побачить, поки активне вікно запущене від адміністратора —
обмеження Windows. Латинські назви брендів усередині слов'янської мови
жодна з перевірених моделей не пише стабільно, тому й існує `replacements.txt`.

**Пороги гучності підібрані під конкретний мікрофон** — той, на якому це
писалося. Якщо нічого не розпізнається або, навпаки, ловиться шум,
дивись у `dictate.log`: там для кожної фрази записані реальні `peak`
і `rms`. Підбирається одним рядком `MIN_PEAK` у `settings.txt`.

### Не українською?

Обмеження тут немає. Модель розуміє близько **99 мов**, Windows —
приблизно 36. Якщо твоєї мови немає у Windows — грецької, івриту,
грузинської, вірменської, казахської, азербайджанської — зміни один
рядок у `settings.txt`, і все працюватиме так само.

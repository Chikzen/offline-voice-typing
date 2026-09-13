# Завдання: MVP голосового набору на Linux (ноут myp)

Підготовлено 2026-09-13 за аналізом коду upstream (`alex80674097219/offline-voice-typing`, коміт `8961a25`).
Форк: `Chikzen/offline-voice-typing`. Локальна копія: `/home/myp/Документи/Python/offline-voice-typing`
(remote `origin` = форк, `upstream` = оригінал). Працювати **на гілці `linux-mvp`** (за потреби — у worktree),
PR у `origin/main`.

## Мета MVP

Двічі натиснув Ctrl → сказав фразу → текст з'явився у полі з фокусом (gedit, браузер, Telegram).
Лише локальний рушій (Parakeet TDT 0.6B v3 через `onnx-asr`), **без root**, без хмари.
Windows-шлях не ламати: зміни через `sys.platform`, щоб можна було запропонувати їх upstream.

**Не в MVP:** Wayland, автозапуск, вставка в терміналі (Ctrl+Shift+V), хмарний резерв, Num Lock/Scroll Lock як тригер.

## Що вже перевірено на ноуті (13.09)

| Факт | Стан |
|---|---|
| ОС / сесія | Debian 13, GNOME, **X11** (`XDG_SESSION_TYPE=x11`) |
| CPU / RAM / диск | i5-1245U, 12 потоків / 31 ГБ / 231 ГБ вільно |
| Python | 3.13.5, `python3-tk` встановлено (`import tkinter` ок) |
| PortAudio | **відсутній** (`libportaudio2` не встановлено) → без нього `sounddevice` не запуститься |
| Буфер обміну | `xclip` є (`~/.local/bin/xclip`, у PATH), `xsel` нема |
| Клавіатура-емуляція | `xdotool` є (`/usr/bin/xdotool`) |
| Звук | `pw-play`, `aplay` є; `paplay` нема |
| PyPI під cp313 | `onnxruntime` 1.30.0 (є manylinux x86_64 cp313 wheel), `onnx-asr` 0.12.0, `sounddevice` 0.5.6, `pynput` 1.8.2, `numpy` 2.5.3 |
| `pyperclip`, `pynput` | у системному python не встановлені — ставити у venv |

Бібліотека `keyboard` (boppreh) на Linux читає `/dev/input/*` і **вимагає root** (README бібліотеки, рядок 94).
Замінити на `pynput` (працює на X11 без root).

## Windows-специфічні місця в `dictate.py` (1171 рядок)

| Рядки | Що | Заміна на Linux |
|---|---|---|
| 23 | `import winsound` — падає при імпорті | імпортувати умовно |
| 193–204 | `beep()` через `winsound.Beep` | тон через `sounddevice.play` (numpy-синус) або `pw-play` |
| 227 | шрифт `Segoe UI` | `DejaVu Sans` |
| 235–251 | `Badge._no_activate` — `ctypes.windll.user32` | no-op (на X11 `overrideredirect`+`-topmost` достатньо; перевірити, що плашка не краде фокус) |
| 552–589 | `paste()` — `keyboard.release/send("ctrl+v")` | `pynput.keyboard.Controller` або `xdotool key ctrl+v`; `pyperclip` сам знайде `xclip` |
| 975–1004 | `LOCK_KEYS`, `_flip_lock`, `make_toggle` (`keybd_event`) | не використовувати на Linux |
| 1029–1074 | `install_double_tap` — `keyboard.hook` | `pynput.keyboard.Listener` (on_press/on_release), та сама логіка «чистого» подвійного тапу (`TAP_GAP`, `TAP_MAX_HOLD`) |
| 1087–1098 | `_single_instance` — `CreateMutexW` | `fcntl.flock` на файл у `$XDG_RUNTIME_DIR` |
| 1137–1155 | `keyboard.add_hotkey` для `HOTKEY` і `LANG_HOTKEY` | на Linux `HOTKEY` пропустити; `Ctrl+Alt+L` через `pynput.keyboard.GlobalHotKeys` (або в MVP не робити) |
| `start_console.cmd`, `requirements.txt` | Windows-запуск; `keyboard` у залежностях | `start.sh`; `keyboard; sys_platform == "win32"`, `pynput; sys_platform == "linux"` |

Решта (сегментація, VAD, `transcribe_local`, `replacements.txt`, `settings.txt`, лог, watchdog) — кросплатформна.

## Кроки з воротами

0. **Користувач (sudo):** `sudo apt install libportaudio2`. Далі агент: `python3 -m venv venv && venv/bin/pip install -r requirements.txt` (після правки requirements).
1. **Ворота 1 — модель.** Записати 5–8 с мови користувача (`pw-record`/`arecord`, 16 кГц моно) у `_test.wav` (ігнорується git як `_*`). Скрипт з `transcribe_local` на цьому файлі: має повернути текст. Заміряти час завантаження моделі і час розпізнавання на i5-1245U при `LOCAL_THREADS` 4 і 8 (автор мав 0.16 с на i9-12900F; тут очікувати повільніше — цифру записати). Якщо `onnx-asr` не ставиться під 3.13 або конфліктує з numpy 2.5 — зафіксувати версії, це не привід міняти рушій.
2. **Ворота 2 — мікрофон.** `sd.query_devices()` показує вхід; `InputStream` на 16 кГц стартує (PipeWire може вимагати явного `sd.default.device`).
3. **Платформний шар.** Мінімально: функції `beep`, `paste`, `single_instance`, `install_double_tap`, `no_activate` вибираються по `sys.platform` (окремий `plat_linux.py` або гілки на місці — обрати менше за діфом). Без редизайну.
4. **Ворота 3 — вставка.** Тестовий виклик `paste("проба")` при фокусі в gedit: текст з'явився, попередній вміст буфера відновлено.
5. **Ворота 4 — тригер.** Подвійний Ctrl вмикає/вимикає (beep + плашка), Ctrl+C/Ctrl+V у інших програмах тригер не смикають (логіка «clean»).
6. **Ворота 5 — наскрізний тест.** Користувач диктує 10 фраз українською в gedit. У `dictate.log`: усі 10 як `local=`, 0 `ERROR paste`, медіана затримки записана. Автовимкнення через `IDLE_OFF=120` спрацювало.
7. `start.sh`, короткий розділ «Linux» у README (5–10 рядків), PR у `origin/main`.

## Ризики

- `pynput` на X11 потребує `DISPLAY` — запускати з графічної сесії, не з systemd без env.
- Плашка tkinter із `overrideredirect` на GNOME/X11 може все ж брати фокус — перевірити на воротах 4; якщо краде, показувати її після `keyboard.send`, не до.
- `phrases/` і `dictate.log` зберігають надиктований текст відкрито (як в upstream) — у MVP лишити, згадати в README.
- Швидкість на i5 невідома; якщо медіана > 1 с — це не блокер MVP, а число для звіту.

## Бюджет (прикидка з сесії 13.09)

15–25 кроків, ~2–3 млн вхідних (переважно кешованих) і ~10–20 тис. вихідних токенів. Найдорожче — цикли «користувач перевіряє голосом → правка».

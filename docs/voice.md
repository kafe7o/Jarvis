# Гласов режим на Jarvis

Говорите на Jarvis на български (или английски), той разбира с Whisper и отговаря на глас.

## Инсталация

```bash
pip install -r requirements.txt -r requirements-voice.txt
export ANTHROPIC_API_KEY=sk-ant-...
```

На Linux за микрофона може да е нужен PortAudio: `sudo apt install libportaudio2`.

## Стартиране

```bash
python -m jarvis.voice                 # говорите в микрофона
python -m jarvis.voice --keyboard      # пишете, Jarvis отговаря на глас
python -m jarvis.voice --say "Добър ден, сър."   # само проба на гласа
```

Говорете след „Слушам...“. Пауза от около секунда приключва изречението.
Кажете „довиждане“ или „стоп“ за край.

## Как работи

1. Микрофонът записва, докато не замълчите (`jarvis/voice/audio.py`).
2. Whisper превръща речта в текст (`jarvis/voice/stt.py`).
3. Текстът отива в чат ядрото `jarvis.assistant.Jarvis` (`jarvis/voice/brain.py`).
4. Отговорът се почиства от Markdown и се изговаря изречение по изречение (`jarvis/voice/tts.py`).

## Настройки

| Опция | Променлива | По подразбиране | Значение |
|---|---|---|---|
| `--language` | `JARVIS_LANGUAGE` | `bg` | Език на говорене; `auto` оставя Whisper да познае и Jarvis отговаря с глас на същия език |
| `--stt` | `JARVIS_STT` | `local` | `local` (faster-whisper, офлайн) или `openai` (Whisper API, нужен `OPENAI_API_KEY`) |
| `--whisper-model` | `JARVIS_WHISPER_MODEL` | `small` | `tiny`, `base`, `small`, `medium`, `large-v3`. За български `medium` или `large-v3` разпознават най-добре |
| `--tts` | `JARVIS_TTS` | `edge` | `edge` (естествени гласове на Microsoft, нужен интернет) или `offline` (pyttsx3) |
| `--voice` | `JARVIS_VOICE` | по езика | Например `bg-BG-BorislavNeural` (мъжки) или `bg-BG-KalinaNeural` (женски) |
| | `JARVIS_TTS_RATE` | `+0%` | Скорост на говора, напр. `+10%` |
| | `JARVIS_SILENCE_SECONDS` | `1.0` | Колко тишина приключва изречението |
| `--brain` | `JARVIS_BRAIN` | `jarvis.assistant:Jarvis` | Друго чат ядро като `module:attr` (функция или обект с `ask`) |

Ако гласът `edge` не е достъпен (няма интернет), Jarvis минава на офлайн гласа на системата.
Офлайн български звучи само ако операционната система има инсталиран български глас.

## Използване от код

```python
from jarvis.assistant import Jarvis
from jarvis.voice import VoiceConfig, VoiceSession
from jarvis.voice.audio import Microphone
from jarvis.voice.stt import create_stt
from jarvis.voice.tts import create_tts

cfg = VoiceConfig.from_env()
VoiceSession(Jarvis().ask, create_stt(cfg), create_tts(cfg), Microphone()).run_voice()
```

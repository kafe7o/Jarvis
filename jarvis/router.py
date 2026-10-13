"""Three levels of answering, so that simple things cost nothing (the routing idea from the owner's video).

1. Commands, with no AI at all: the time and date, the weather, music and videos, media keys and volume,
   opening sites and programs (also on the phone), locking the computer, reminders, greetings, spending and
   switching „отговаряй вместо мен“ on and off.
   Matched by rules here and done at once through the normal tools, so permissions and confirmations apply.
2. A quick answer: a short question goes to the cheapest model with a short prompt and no tools. If it needs
   tools after all, the model says ESCALATE and level 3 takes over (see brain.Jarvis.quick_answer).
3. The full agent with every tool (brain.Jarvis._loop), only for real work.

Bulgarian typed in Latin letters ("pusni muzika", "kolko e chasa") is understood too.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

# How each Cyrillic letter may be typed in Latin letters.
LATIN = {
    "а": "a", "б": "b", "в": "[vw]", "г": "g", "д": "d", "е": "e", "ж": "(?:zh|j|z)", "з": "z", "и": "i",
    "й": "[iyj]", "к": "[kq]", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "[hx]", "ц": "(?:ts|c)", "ч": "(?:ch|4)", "ш": "(?:sh|6)", "щ": "(?:sht|6t)",
    "ъ": "[auyi1]?", "ь": "", "ю": "(?:yu|iu|ju|u)", "я": "(?:ya|ia|ja|q|a)",
}


def bg(pattern: str) -> str:
    """A regular expression for Bulgarian words that also matches them typed in Latin letters: "пусни" or "pusni".

    Only Cyrillic letters change; everything else in ``pattern`` is regular-expression syntax and stays."""
    return "".join(f"(?:{ch}|{LATIN[ch]})" if ch in LATIN else ch for ch in pattern)


def rx(pattern: str) -> re.Pattern:
    return re.compile(bg(pattern), re.I)


MONTHS = ["януари", "февруари", "март", "април", "май", "юни", "юли", "август", "септември", "октомври",
          "ноември", "декември"]
WEEKDAYS = ["понеделник", "вторник", "сряда", "четвъртък", "петък", "събота", "неделя"]

WAKE = rx(r"^(?:ей,? |хей,? )?(?:джарвис|жарвис|jarvis)\b[\s,.!:-]*")
POLITE = rx(r"(?:^|\s)(?:моля те|моля|ако обичаш|please)(?=\s|$|[,.!?])")
TRAILER = rx(r"[\s,]+(?:джарвис|jarvis|сър|бе|де|веднага)$")
# Fillers a sentence may start with ("а кое те забави", "ама защо", "и какво сега"): dropped before the rules look.
LEAD = rx(r"^(?:а|ама|абе|и|е|ми|но|та|значи|добре|ок|окей|и така|тогава|между другото|всъщност)[\s,]+(?=\S)")

TIME = rx(r"^(?:колко|кой) (?:е )?часа?(?:ът|а)?(?: е)?(?: (?:сега|в момента|точно))?$|^часът\??$")
DATE = rx(r"^(?:коя (?:е )?дата(?:та)?(?: е| сме)?|коя е датата|кой ден (?:е|сме)|какъв ден (?:е|сме))(?: днес| сега)?$")
WEATHER = rx(r"(?:^|\s)(?:времето|прогноза(?:та)?|вали ли|ще вали|валеж|температура(?:та)?|колко градуса|"
             r"студено ли|топло ли|ще има ли сняг|сняг ли)(?=\s|$|[?!.,])")
WEATHER_PLACE = rx(r"(?:^|\s)(?:в|във|за) (?!момента|днес|утре|вдругиден|седмицата|уикенда|събота|неделя|навън)"
                   r"([^\s,?.!]+(?: [^\s,?.!]+)?)")
NOT_PLACE = rx(r"^(?:днес|утре|вдругиден|сега|ли|ще|вали|е|какво|какво е|навън|момента|седмицата|уикенда)$")
NOT_WEATHER = rx(r"(?:^|\s)(?:напомни|изпрати|прати|пиши|кажи на|запиши|сподели)")

PAUSE = rx(r"^(?:пауза|(?:спри|паузирай|стопирай) (?:музиката|песента|видеото|клипа|филма|youtube))$")
RESUME = rx(r"^(?:(?:продължи|пусни|включи) (?:музиката|музика|песента|видеото|клипа|филма)|пусни пак)$")
NEXT = rx(r"^(?:(?:пусни )?следващ(?:ата|ия|ото|а)?|друга песен|смени песента|прескочи)(?: (?:песен|клип|видео))?$")
PREVIOUS = rx(r"^(?:(?:пусни )?предишн(?:ата|ия|а)|предната|върни песента)(?: (?:песен|клип|видео))?$")
LOUDER = rx(r"^(?:още )?(?:по-? ?силно|усили(?: (?:звука|музиката))?|увеличи (?:звука|музиката)|звука нагоре)$")
QUIETER = rx(r"^(?:още )?(?:по-? ?тихо|намали(?: (?:звука|музиката))?|звука надолу)$")
MUTE = rx(r"^(?:заглуши|без звук|спри звука|mute)(?: всичко| компютъра)?$")
VOLUME = rx(r"^(?:(?:сложи|направи|намали|увеличи|усили|пусни) )?(?:звука|звук|силата на звука)(?: на| до)? "
            r"(\d{1,3}) ?(?:%|процента)?$")

ON_DEVICE = rx(r"\s+(?:на|в|по) (телефона|телефон|тв-то|тв|телевизора|телевизор)$")
OPEN = rx(r"^(отвори|стартирай|пусни|включи|open)(?: ми)? (.+)$")
OPEN_VERB = rx(r"^(?:отвори|стартирай|open)$")
PLAY = rx(r"^(?:пусни|сложи|play)(?: ми)? (.+)$")
NOT_MEDIA = rx(r"таймер|аларм|напомн|съобщ|смс|sms|имейл|писмо|мейл|режим|лампа|лампите|осветлени|светлин|климатик|"
               r"прахосмукач|пералня|бойлер|отоплени|парно|вентилатор|щори|кафемашина|робот|колата|камерат|"
               r"компютъра|лаптопа|wifi|wi-fi|блутут|bluetooth|^телефона$|^тв$|^(?:го|я|ги|това|него|нея|тях)$")

MAIL = rx(r"(?:^|\s)(?:имейл|мейл|е-мейл|писма(?:та)?|поща(?:та)?|gmail|джимейл|inbox)")
MAIL_LOOK = rx(r"(?:^|\s)(?:виж|провери|прочети|покажи|кажи|колко|имам ли|какви|какво|нови|новите|непрочетени)(?=\s|$|[,.!?:])")
MAIL_NOT = rx(r"(?:^|\s)(?:изпрати|прати|пиши|напиши|отговори|препрати|изтрий|архивирай|маркирай|намери|търси|"
              r"резюмирай|обобщи|анализирай|от кого е|за какво|съдържание|прикачен|снимк|файл|пише|последния|последното)")
MAIL_BROWSER = rx(r"(?:^|\s)(?:браузър|браузъра|браузера|отвори|gmail|джимейл)")
LOCK = rx(r"^(?:заключи|lock) (?:компютъра|лаптопа|компа|екрана|пц-то|pc-то)$")
SLEEP = rx(r"^(?:приспи|сложи да спи) (?:компютъра|лаптопа|компа)$")
SHUTDOWN = rx(r"^(?:изключи|угаси|спри) (?:компютъра|лаптопа|компа)$")
RESTART = rx(r"^(?:рестартирай|restart)(?: (?:компютъра|лаптопа|компа))?$")

REMIND = rx(r"^напомни ми (.+)$")
AFTER = rx(r"(?:^|\s)след (\d+|една|един|две|два|три|пет|десет|петнадесет|двадесет|тридесет|половин)? ?"
           r"(минути|минута|мин\.?|часа|час|секунди)(?=\s|$|[,.!])")
AT = rx(r"(?:^|\s)(?:(утре|днес|вдругиден) )?(?:в|във|at) (\d{1,2})(?:(?::|\.|,| и )(\d{2}))?"
        r"(?: (?:часа|ч\.?))?(?: (сутринта|вечерта|следобед|на обяд|през нощта))?(?=\s|$|[,.!])")
DAY_FIRST = rx(r"^\s*(утре|вдругиден)(?=\s|$|[,.!])")
NUMBERS = {"една": 1, "един": 1, "две": 2, "два": 2, "три": 3, "пет": 5, "десет": 10, "петнадесет": 15,
           "двадесет": 20, "тридесет": 30}

GREETING = rx(r"^(?:здрасти|здрасте|здравей(?:те)?|хей|ей|привет|добър ден|добър вечер|добро утро|hello|hi|hey)"
              r"(?: (?:джарвис|jarvis))?$")
MORNING = rx(r"^добро утро")
THANKS = rx(r"^(?:благодаря(?: ти)?|мерси|thanks|thank you|браво|супер си|страхотен си)(?: (?:джарвис|jarvis))?$")
HOW_ARE_YOU = rx(r"^(?:как си(?: днес)?|как я караш|как върви)$")
GOOD_NIGHT = rx(r"^лека нощ(?: (?:джарвис|jarvis))?$")
SPENDING = rx(r"^(?:колко (?:похарчих|похарчи|харча|харчиш|струва(?:ше)?|ми струва)(?: днес| досега| тази седмица)?|"
              r"разход(?:ът)?(?: днес)?|колко кредити (?:похарчих|похарчи|остават))$")
REPLY_ON = rx(r"^(?:отговаряй|отговарай) (?:вместо мен|на съобщенията(?: вместо мен)?)$|"
              r"^(?:включи|пусни) (?:автоматичните )?отговори(?:те)?(?: на съобщенията)?$")
REPLY_OFF = rx(r"^(?:спри|престани|изключи) (?:да отговаряш|автоматичните отговори|отговорите)(?: вместо мен| на съобщенията)?$|"
               r"^не отговаряй (?:вместо мен|на съобщенията)$")
UNDO_UPGRADE = rx(r"^(?:върни|отмени|махни)(?: последната| си)? (?:надстройка(?:та)?|промяна(?:та)?)(?: (?:на|в) себе си)?$")
UPDATE_SELF = rx(r"^(?:обнови се|ъпдейтни се|самоъпдейтни се|обнови (?:jarvis|джарвис)|"
                 r"провери за (?:нова версия|обновления|ъпдейт))$")
SPLIT = rx(r"\s*,?\s+(?:и после|после|след това|и)\s+")

# Sites and programs that "отвори …" opens without asking the AI. Keys are regular expressions (Bulgarian
# words also match in Latin letters); values go to the open_target tool.
SITES = [
    (r"youtube|ютюб|ютуб", "https://www.youtube.com"),
    (r"gmail|джимейл|гмаил|пощата в google", "https://mail.google.com"),
    (r"facebook|фейсбук|фейса", "https://www.facebook.com"),
    (r"instagram|инстаграм", "https://www.instagram.com"),
    (r"tiktok|тикток|тик ток", "https://www.tiktok.com"),
    (r"google|гугъл", "https://www.google.com"),
    (r"google maps|maps|картите|карти|гугъл мапс", "https://www.google.com/maps"),
    (r"netflix|нетфликс", "https://www.netflix.com"),
    (r"chatgpt|чат gpt|чатгпт", "https://chatgpt.com"),
    (r"abv|абв|abv\.bg", "https://www.abv.bg"),
    (r"olx|олх", "https://www.olx.bg"),
    (r"wikipedia|уикипедия", "https://bg.wikipedia.org"),
    (r"преводача|преводач|google translate|translate", "https://translate.google.com"),
    (r"браузъра|браузър|интернет", "https://www.google.com"),
]
PROGRAMS = [
    (r"калкулатора|калкулатор|calculator|calc", "calc"),
    (r"бележника|бележник|notepad", "notepad"),
    (r"paint|пейнт", "mspaint"),
    (r"chrome|хром|гугъл хром", "chrome"),
    (r"edge|едж", "msedge"),
    (r"word|уърд", "winword"),
    (r"excel|ексел", "excel"),
    (r"powerpoint|пауърпойнт", "powerpnt"),
    (r"spotify|спотифай", "spotify:"),
    (r"steam|стийм", "steam://open/main"),
    (r"настройките|настройки|settings", "ms-settings:"),
    (r"камерата|камера", "microsoft.windows.camera:"),
    (r"файловете|файлове|explorer|документите|моите документи", "explorer"),
]
TARGETS = [(re.compile(f"^(?:{bg(k)})$", re.I), v, kind) for kind, table in (("site", SITES), ("program", PROGRAMS))
           for k, v in table]
DOMAIN = re.compile(r"^(?:https?://)?(?:www\.)?[\w-]+(?:\.[\w-]+)*\.(?:com|bg|net|org|eu|io|tv|me|info|dev|app)(?:/\S*)?$", re.I)

# Level 2: questions a model can answer from what it knows, without tools.
QUESTION = rx(r"^(?:какво|кво|к'во|що|какъв|каква|какви|как|защо|кой|коя|кое|кои|кога|къде|колко|може ли|можеш ли|мога ли|"
              r"има ли|знаеш ли|обясни|разкажи|преведи|измисли|предложи|посъветвай|сравни|дай ми (?:идея|идеи|съвет|"
              r"пример)|кажи ми (?:виц|нещо|за|какво|как|защо|кой)|напиши (?:ми )?(?:стих|стихотворение|текст|есе|история|"
              r"приказка|поздрав|пожелание|честитка|песен|виц)|what|how|why|who|explain|translate|tell me)(?=\s|$|[,?!.])")
NEEDS_TOOLS = rx(r"файл|папк|екран|снимк|телефон|смс|sms|съобщени|имейл|мейл|писм|поща|gmail|календар|срещ|задач|"
                 r"напомн|запомн|помниш|спомн|контакт|обади|звънни|прати|изпрати|плат|купи|поръч|резерв|отвори|"
                 r"затвори|инсталир|свали|изтегл|качи|компютър|лаптоп|програм|сайт|линк|интернет|google|гугъл|"
                 r"потърси|търси|намери|провери|новин|днес|сега|вчера|утре|тази седмица|цена|цени|курс|борс|"
                 r"биткойн|bitcoin|резултат|мач|времето|градус|последн|актуал|в момента|моя|моят|моите|мойта|"
                 r"моето|паметта|youtube|ютюб|https?://|www\.|\.com\b|\.bg\b")
SMALL_TALK = rx(r"^(?:благодаря|мерси|сенкс|thanks?|thank you|здрасти|здравей|здрасе|здр|хей|ехо|ало|добро утро|"
                r"добър ден|добър вечер|лека нощ|чао|до скоро|ок|окей|ok|okay|добре|ясно|разбрах|супер|страхотно|"
                r"браво|евала|да|не|аха|хаха|нищо|няма нужда|забрави|остави)(?:\s+(?:ти|ви|много|бе|де|брат|"
                r"джарвис|jarvis|сър))*$")
# Small talk asked as a question ("как си", "кво става"): quick even next to a time word like "сега" or "днес".
TIME_WORDS = rx(r"(?:^|\s)(?:сега|днес|вчера|утре|в момента|тази седмица)(?=\s|$|[,?!.])")
CHAT = rx(r"(?:^|\s)(?:как си|как сте|как е|как върви|как мина|кво става|какво става|какво правиш|какво ново|"
          r"какво има|how are you|what's up|whats up)(?=\s|$|[,?!.])")
STOP_WORK = rx(r"^(?:спри|стоп|спирай|престани|прекрати|откажи|cancel)(?: (?:всичко|всички|това|го|ги|задачата|задачите|"
               r"работата|да работиш|с всичко|с това|се|веднага|сега|моментално|незабавно))*$")
THINK_HARDER = rx(r"помисли (?:добре|задълбочено|сериозно|внимателно)|задълбочено|в детайли|подробен анализ|"
                  r"think hard|ultrathink")


@dataclass
class Step:
    """One thing to do for a command: run ``tool`` with ``args`` (or nothing), then reply with ``say(output)``."""

    say: Callable[[str], str]
    tool: str | None = None
    args: dict = field(default_factory=dict)
    text: str = ""  # the words this step came from


@dataclass
class Info:
    """What the rules need to know about the moment."""

    user_name: str = "сър"
    now: Callable[[], datetime] = datetime.now
    last_answer: str = ""
    owner: bool = True
    devices: Callable[[], dict] = dict  # Android devices by name ("phone", "tv")
    spending: Callable[[], str] = lambda: ""  # noqa: E731
    weather_now: Callable[[], str] = lambda: ""  # noqa: E731


def clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    text = WAKE.sub("", text)
    text = POLITE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" ,.!…")
    text = TRAILER.sub("", text).strip(" ,.!…")
    text = LEAD.sub("", text)
    return text


def words(text: str) -> int:
    return len(text.split())


def match(text: str, info: Info | None = None) -> list[Step] | None:
    """The steps for a level-1 command, or None when the AI is needed."""
    info = info or Info()
    said = clean(text)
    if not said or len(said) > 200 or "\n" in said:
        return None
    mail = _mail(said.rstrip("?").strip(), info)
    if mail:
        return mail
    one = _one(said, info)
    if one:
        return [one]
    parts = [p for p in SPLIT.split(said) if p]
    if len(parts) < 2:
        return None
    steps = [_one(p, info) for p in parts]
    return steps if all(steps) else None


def _one(said: str, info: Info) -> Step | None:
    s = said.rstrip("?").strip()
    for rule in (_talk, _clock, _media, _weather, _power, _remind, _open, _play):
        step = rule(s, info)
        if step:
            step.text = step.text or said
            return step
    return None


# --- the rules -----------------------------------------------------------------------------------------

def _talk(s: str, info: Info) -> Step | None:
    name = info.user_name
    if GREETING.match(s):
        hour = info.now().hour
        hello = "Добро утро" if 4 <= hour < 11 else "Добър вечер" if hour >= 18 or hour < 4 else "Добър ден"
        if MORNING.match(s):
            weather = info.weather_now()
            return Step(lambda _o: f"Добро утро, {name}. Часът е {info.now():%H:%M}." + (f" {weather}" if weather else ""))
        return Step(lambda _o: f"{hello}, {name}. С какво да помогна?")
    if THANKS.match(s) and not info.last_answer.rstrip().endswith("?"):
        return Step(lambda _o: random.choice([f"Винаги на линия, {name}.", f"За нищо, {name}.", f"Удоволствие е, {name}."]))
    if HOW_ARE_YOU.match(s):
        return Step(lambda _o: f"Всички системи работят нормално, {name}. С какво да помогна?")
    if GOOD_NIGHT.match(s):
        return Step(lambda _o: f"Лека нощ, {name}. Ще бъда тук.")
    if SPENDING.match(s) and info.owner:
        return Step(lambda _o: info.spending() or "Днес не съм похарчил нищо.")
    if (REPLY_ON.match(s) or REPLY_OFF.match(s)) and info.owner:  # „отговаряй вместо мен“ (replies.py)
        return Step(lambda out: out, "auto_reply", {"action": "on" if REPLY_ON.match(s) else "off"})
    if UNDO_UPGRADE.match(s) and info.owner:  # works even when an upgrade broke the AI part (upgrades.py)
        return Step(lambda out: out, "undo_upgrade", {})
    if UPDATE_SELF.match(s) and info.owner:
        return Step(lambda out: out, "update_jarvis", {})
    return None


def _mail(s: str, info: Info) -> list[Step] | None:
    """„Виж имейлите ми“, „колко непрочетени имейла имам“, „отвори браузъра и виж пощата“: the count and the newest
    senders straight from the inbox in a second or two (the owner wants this in under 5 s), Gmail opened when asked."""
    if not info.owner or words(s) > 25 or not MAIL.search(" " + s) or not MAIL_LOOK.search(" " + s) \
            or MAIL_NOT.search(" " + s):
        return None
    steps = [Step(lambda out: out, "mail_count", {"newest": 3}, s)]
    if MAIL_BROWSER.search(" " + s):  # the answer first, then Gmail in the browser for the owner to look at
        steps.append(Step(lambda _o: "", "open_target", {"target": "https://mail.google.com"}, s))
    return steps


def _clock(s: str, info: Info) -> Step | None:
    if TIME.match(s):
        return Step(lambda _o: f"Часът е {info.now():%H:%M}.")
    if DATE.match(s):
        now = info.now()
        return Step(lambda _o: f"Днес е {WEEKDAYS[now.weekday()]}, {now.day} {MONTHS[now.month - 1]} {now.year} г.")
    return None


def _media(s: str, info: Info) -> Step | None:
    simple = [(PAUSE, "play_pause", "Пауза."), (RESUME, "play_pause", "Продължавам."), (NEXT, "next", "Следващата."),
              (PREVIOUS, "previous", "Предишната."), (LOUDER, "volume_up", "По-силно."),
              (QUIETER, "volume_down", "По-тихо."), (MUTE, "mute", "Без звук.")]
    for pattern, key, reply in simple:
        if pattern.match(s):
            times = 5 if key in ("volume_up", "volume_down") else 1
            return Step(lambda _o, r=reply: r, "media_control", {"key": key, "times": times})
    found = VOLUME.match(s)
    if found and 0 <= int(found[1]) <= 100:
        level = int(found[1])
        return Step(lambda _o: f"Звукът е на {level}%.", "set_volume", {"level": level})
    return None


def _weather(s: str, info: Info) -> Step | None:
    if not WEATHER.search(" " + s) or words(s) > 9 or NOT_WEATHER.search(" " + s):
        return None
    day = 1 if re.search(bg(r"(?:^|\s)утре(?=\s|$)"), s, re.I) else 2 if re.search(bg(r"вдругиден"), s, re.I) else 0
    week = re.search(bg(r"седмица|уикенд|следващите дни"), s, re.I)
    place = None
    found = WEATHER_PLACE.search(" " + s)
    if found:
        bits = [b for b in found[1].split() if not NOT_PLACE.match(b)]
        place = " ".join(bits[:2]) or None
    days = 7 if week else 3

    def say(output: str) -> str:
        lines = [line for line in output.splitlines() if line.strip()]
        if week or len(lines) < 2:
            return "\n".join(lines)
        where = lines[0].split(":", 1)[0]
        if day == 0:
            return "\n".join(lines[:2])
        return f"{where}. {lines[min(day + 1, len(lines) - 1)]}"

    return Step(say, "weather", {"place": place, "days": days} if place else {"days": days})


def _power(s: str, info: Info) -> Step | None:
    if LOCK.match(s):
        return Step(lambda _o: "Заключих компютъра.", "lock_computer", {"action": "lock"})
    if SLEEP.match(s):
        return Step(lambda _o: "Приспивам компютъра.", "lock_computer", {"action": "sleep"})
    if SHUTDOWN.match(s):
        return Step(lambda _o: "Изключвам компютъра след 10 секунди.", "power_off", {"action": "shutdown"})
    if RESTART.match(s):
        return Step(lambda _o: "Рестартирам компютъра след 10 секунди.", "power_off", {"action": "restart"})
    return None


def when(rest: str, now: datetime) -> tuple[datetime, str] | None:
    """(time, the rest without the time words) for "след 10 минути …" or "утре в 9 …"; None if there is no time."""
    found = AFTER.search(" " + rest)
    if found:
        amount = found[1] or "1"
        if amount.lower() in ("половин", "polovin"):
            minutes = 30
        else:
            number = int(amount) if amount.isdigit() else NUMBERS.get(amount.lower(), next(
                (v for k, v in NUMBERS.items() if re.fullmatch(bg(k), amount, re.I)), 1))
            unit = found[2].lower()
            minutes = number * 60 if re.match(bg("час"), unit, re.I) else number / 60 if re.match(bg("сек"), unit, re.I) else number
        at = now + timedelta(minutes=minutes)
        return at, (" " + rest)[:found.start()] + (" " + rest)[found.end():]
    lead = DAY_FIRST.match(rest)  # "утре да платя тока в 10"
    day_word = lead[1] if lead else ""
    if lead:
        rest = rest[lead.end():]
    found = AT.search(" " + rest)
    if not found:
        return None
    hour, minute = int(found[2]), int(found[3] or 0)
    if hour > 23 or minute > 59:
        return None
    part = (found[4] or "").lower()
    if part and re.match(bg("вечерта|следобед"), part, re.I) and hour < 12:
        hour += 12
    day_word = found[1] or day_word
    at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if re.fullmatch(bg("утре"), day_word, re.I):
        at += timedelta(days=1)
    elif re.fullmatch(bg("вдругиден"), day_word, re.I):
        at += timedelta(days=2)
    elif at <= now:
        if not part and hour < 12 and at + timedelta(hours=12) > now:
            at += timedelta(hours=12)  # "в 6" in the afternoon means 18:00
        else:
            at += timedelta(days=1)
    return at, (" " + rest)[:found.start()] + (" " + rest)[found.end():]


def _remind(s: str, info: Info) -> Step | None:
    found = REMIND.match(s)
    if not found:
        return None
    now = info.now()
    timed = when(found[1], now)
    if not timed:
        return None
    at, what = timed
    what = re.sub(r"\s+", " ", what).strip(" ,.")
    if not what:
        return None
    if at.date() == now.date():
        day = ""
    elif at.date() == (now + timedelta(days=1)).date():
        day = "утре "
    else:
        day = f"на {at.day} {MONTHS[at.month - 1]} "
    return Step(lambda _o: f"Добре, ще ти напомня {day}в {at:%H:%M}.", "add_reminder",
                {"text": what, "at": at.replace(second=0, microsecond=0).isoformat()})


def _device(s: str, info: Info) -> tuple[str, str | None]:
    """The words without "на телефона"/"на телевизора" and that device's name, if it is connected."""
    found = ON_DEVICE.search(s)
    if not found:
        return s, None
    kind = "tv" if re.match(bg("тв|телевизор"), found[1], re.I) else "phone"
    names = info.devices()
    return s[:found.start()].strip(), (kind if kind in names else "")


def _open(s: str, info: Info) -> Step | None:
    found = OPEN.match(s)
    if not found:
        return None
    target, device = _device(found[2].strip(), info)
    known = next(((value, kind) for pattern, value, kind in TARGETS if pattern.match(target)), None)
    if device == "":
        return None  # that phone or TV is not connected: the AI explains
    if device:
        # "пусни Eminem на телефона" is music (see _play); "пусни tiktok на телефона" opens the app
        if NOT_MEDIA.search(target) or words(target) > 3 or not (OPEN_VERB.match(found[1]) or known):
            return None
        where = "телефона" if device == "phone" else "телевизора"
        return Step(lambda _o: f"Отворих {target} на {where}.", "android",
                    {"device": device, "action": "open_app", "name": target})
    if known:
        return Step(lambda _o: f"Отварям {target}.", "open_target", {"target": known[0]})
    if DOMAIN.match(target):
        url = target if "://" in target else "https://" + target
        return Step(lambda _o: f"Отварям {target}.", "open_target", {"target": url})
    return None


def _play(s: str, info: Info) -> Step | None:
    found = PLAY.match(s)
    if not found:
        return None
    query, device = _device(found[1].strip(), info)
    if device == "" or not query or NOT_MEDIA.search(query):
        return None
    where = {"phone": " на телефона", "tv": " на телевизора"}.get(device or "", "")

    def say(output: str) -> str:
        title = re.search(r"“(.+?)”", output)
        if title:
            return f"Пускам „{title[1]}“{where}."
        return f"Отворих YouTube с резултатите за „{query}“{where}."

    return Step(say, "play_youtube", {"query": query, "device": device} if device else {"query": query})


# --- levels 2 and 3 ------------------------------------------------------------------------------------

# "можеш ли да го оправиш" asks for the deed, not for an answer about ability.
CAN_YOU = rx(r"^(?:може ли|можеш ли|би ли|ще можеш ли|ще може ли|имаш ли как|can you|could you|would you)(?=\s|$)")
DEED = rx(r"(?:^|\s)(?:да |го |ги |ми |me |it )?(?:оправ|направ|пусн|спр|провер|прат|изпрат|намер|отвор|включ|изключ|"
          r"инсталир|свал|изтегл|кач|запиш|запаз|смен|промен|поправ|покаж|изтри|обад|звънн|напиш|слож|плат|куп|поръч|"
          r"резервир|подред|почист|стартир|рестартир|обнов|fix|do|send|open|run|install|start|stop)\w*")


# "направи ли го", "стана ли", "готово ли е": a question whether the work got done, answered by checking, not from memory.
DONE_Q = rx(r"(?:^|\s)(?:направи|стана|готов[оаи]?|готово е|успя|свърши|сработи|оправи|прати|изпрати|записа|запази|"
            r"намери|пусна|включи|работи|върви|мина|има)(?: го| я| ги)? ли(?=\s|$|[?,.!])")


def asks_if_done(text: str) -> bool:
    return bool(DONE_Q.search(clean(text)))


def quick(text: str) -> bool:
    """True when a question can go to the quick level (level 2): it asks something to know, not to do."""
    said = clean(text)
    if (CAN_YOU.match(said) and DEED.search(said)) or DONE_Q.search(said):
        return False
    if said and words(said) <= 12 and CHAT.search(said) and not NEEDS_TOOLS.search(TIME_WORDS.sub(" ", said)):
        return True
    return bool(said) and words(said) <= 40 and bool(QUESTION.match(said)) and not NEEDS_TOOLS.search(said)


# Imperatives that ask Jarvis to do something ("пусни", "провери ми", "обади се").
IMPERATIVE = rx(r"(?:^|\s)(?:пусни|направи|провери|прати|изпрати|отвори|затвори|качи|свали|изтегли|обади се|звънни|"
                r"напиши|намери|потърси|инсталирай|сложи|смени|промени|изтрий|запиши|запази|покажи|включи|изключи|"
                r"стартирай|рестартирай|оправи|поправи|обнови|работи|свърши|започни|продължи|купи|плати|поръчай|"
                r"резервирай|подреди|почисти|планирай|пресметни|изчисли|преведи|прочети|напомни|запомни|разгледай|"
                r"изпълни|довърши|тествай|пробвай|анализирай|проучи|научи|създай|генерирай|подготви|уреди|организирай|"
                r"изпрати|отговори|спри|погледни|виж|прегледай|кажи ми|сравни|обобщи|резюмирай|отбележи|отмени|"
                r"премести)(?=\s|$|[,.!?])")
WANT = rx(r"(?:^|\s)(?:искам|трябва|нека|хайде|айде|дай)(?=\s|$)")
# Talk about Jarvis himself or the conversation ("кое те забави", "защо го каза", "не те разбрах"): an answer in words.
META = rx(r"(?:^|\s)(?:те|ти|си|каза|казваш|направи|забави|разбра|разбрах|говоря|говорим|имах предвид|пиша|писах|"
          r"попитах|отговори ми|отговаряш)(?=\s|$|[,.!?])")


def wants_action(text: str) -> bool:
    """True when the message asks Jarvis to do something, so an answer with no tool used deserves one more look
    (brain.ACT_CHECK): an order ("пусни", "провери"), "можеш ли да го оправиш", "искам да...", a mission, or a
    question that needs live information ("как е времето сега"). False for small talk, comments, and questions
    about Jarvis himself or the conversation ("кое те забави толкова", "защо го каза")."""
    said = clean(text)
    if not said or SMALL_TALK.match(said):
        return False
    if IMPERATIVE.search(said) or is_mission(said) or ((CAN_YOU.match(said) or WANT.search(said)) and DEED.search(said)):
        return True
    if DONE_Q.search(said):  # "направи ли го": look at the result (connections, the window) before answering
        return True
    if META.search(said):
        return False
    return bool(QUESTION.match(said)) and bool(NEEDS_TOOLS.search(said)) or bool(DEED.search(said))


CORRECTS = rx(r"(?:^|\s)(?:не (?:така|това|е така|е това|си разбрал|ме разбра|ме слушаш|стана|работи|го направи)|"
              r"грешно|сбърка|казах ти|пак не|отново не|защо не|не правиш|не върши)(?=\s|$|[,.!?])")


def corrects(text: str) -> bool:
    """The owner says Jarvis got something wrong ("не така", "казах ти", "пак не стана"): worth a lesson."""
    return bool(CORRECTS.search(clean(text)))


def stops_work(text: str) -> bool:
    """"спри", "спри всичко", "stop": end the work in progress and drop what waits after it (heard at once, even
    while a task runs). "спри музиката" and the like are not this (they pause the music)."""
    return bool(STOP_WORK.match(clean(text)))


MISSION = rx(r"докато не (?:свършиш|е готово|стане|приключиш|си доволен|е перфектно|е идеално|го довършиш)|без да спираш|"
             r"да не спираш|не спираш докато|свърши си работата|довърши си работата|"
             r"не спирай|до край(?:я)?|до последно|работи сам|работи до|цялата работа|довърши (?:всичко|всичката)|"
             r"провери (?:всичко|пак всичко|наново)|перфектно|идеално|мисия|непрекъснато|нонстоп|non-?stop|"
             r"направи (?:си )?план(?: и)? (?:го изпълни|започни|работи)")


def is_mission(text: str) -> bool:
    """A big job to be carried through on its own ("работи докато не стане", "без да спираш", "провери всичко"):
    more steps, and a check of the whole result before the answer counts (brain.VERIFY_CHECK)."""
    return bool(MISSION.search(clean(text)))


def think_harder(text: str) -> bool:
    """The owner asks for careful thinking: the full agent then thinks harder for this one request."""
    return bool(THINK_HARDER.search(text or ""))


# --- Bulgarian typed in Latin letters ("iskam da vidim kak she stane") -------------------------------------
# Common Bulgarian words as people type them without Cyrillic. The weak models Jarvis runs on for free read
# them better when a rough Cyrillic version goes along with the message (see brain.Jarvis._messages).
_STRONG_LATIN = {
    "iskam", "iskash", "iska", "iskame", "moje", "mozhe", "moga", "mojesh", "mozhesh", "moga", "she", "shte", "ste",
    "sega", "dnes", "utre", "tova", "tuk", "tam", "kak", "kakvo", "kolko", "koga", "kade", "kyde", "kude", "zashto",
    "zasto", "za6to", "pusni", "napravi", "kaji", "kazhi", "otvori", "prati", "izprati", "proveri", "napishi",
    "napi6i", "pokaji", "pokazhi", "nameri", "potarsi", "potursi", "vij", "vizh", "molya", "molq", "mersi",
    "blagodarya", "zdravei", "zdrasti", "dobre", "ima", "nyama", "niama", "nqma", "ako", "kogato", "sled", "predi",
    "mnogo", "malko", "oshte", "oste", "o6te", "veche", "samo", "vsichko", "vsi4ko", "neshto", "nesto", "ne6to",
    "tryabva", "trqbva", "triabva", "probvai", "probvaj", "sushto", "sashto", "sa6to", "sushto", "vidim", "vidq",
    "vidya", "vida", "stane", "stava", "raboti", "rabotish", "ne", "da", "li", "na", "sas", "ot", "pri", "bez",
    "zaedno", "napravish", "napravi6", "greshka", "gre6ka", "ne6to", "kakvi", "koi", "koq", "koya", "koe", "tozi",
    "tazi", "tezi", "onzi", "mi", "ti", "go", "gi", "ni", "vi", "sum", "sam", "si", "e", "sa", "beshe", "be6e",
    "shte", "kato", "che", "4e", "zaradi", "obache", "oba4e", "mislq", "mislya", "znam", "znae6", "znaesh", "umen",
    "po-umen", "poumen", "dali", "kazah", "kaza", "pravi", "pravish", "pravi6", "napraveno", "gotovo", "spri", "stop",
    "kvo", "staa", "zdr", "zdravey", "zdravej", "kaje", "kazhe", "znachi", "zna4i", "stana", "zashtoto", "za6toto",
    "shtoto", "6toto", "tochno", "to4no", "kakvoto", "koeto", "nekvo", "neshto", "mojem", "mozhem",
}
_ENGLISH = {"the", "is", "are", "you", "and", "to", "of", "what", "please", "with", "this", "that", "for", "can",
            "do", "it", "my", "me", "in", "on", "a", "an", "how", "why", "when", "where", "not", "have", "has", "be"}
_WEAK = {"i", "e", "s", "v", "da", "ne", "na", "li", "mi", "ti", "go", "si", "sa", "ot", "za", "po", "no", "ni"}
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_LATIN_WORD = re.compile(r"[a-z0-9\-]+")

# Latin letters and digits -> Cyrillic, longest sequences first ("sht" before "sh" before "s").
_TO_CYR = [
    ("sht", "щ"), ("6t", "щ"), ("sh", "ш"), ("ch", "ч"), ("zh", "ж"), ("ts", "ц"), ("yu", "ю"), ("iu", "ю"), ("ju", "ю"),
    ("ya", "я"), ("ia", "я"), ("ja", "я"), ("yo", "йо"), ("jo", "йо"), ("a", "а"), ("b", "б"), ("v", "в"), ("w", "в"),
    ("g", "г"), ("d", "д"), ("e", "е"), ("z", "з"), ("i", "и"), ("j", "ж"), ("k", "к"), ("q", "я"), ("l", "л"),
    ("m", "м"), ("n", "н"), ("o", "о"), ("p", "п"), ("r", "р"), ("s", "с"), ("t", "т"), ("u", "у"), ("f", "ф"),
    ("h", "х"), ("x", "х"), ("c", "ц"), ("y", "ъ"), ("4", "ч"), ("6", "ш"),
]
_TO_CYR_RX = re.compile("|".join(re.escape(latin) for latin, _ in _TO_CYR), re.I)
_TO_CYR_MAP = {latin: cyr for latin, cyr in _TO_CYR}
# Words that stay as they are: names of products and sites, and the words that are English anyway.
_KEEP = re.compile(r"^(?:jarvis|gemini|groq|claude|google|youtube|gmail|chrome|windows|wifi|ok|api|pc|tv|usb|"
                   r"url|http\S*|www\.\S*|\S+\.(?:com|bg|net|org|exe|py|txt|md)|\d+|[a-z]\d+|\d+[a-z]+)$", re.I)


def latin_bulgarian(text: str) -> bool:
    """True for a message written in Bulgarian with Latin letters (no Cyrillic, several Bulgarian words, few
    English ones): "iskam da vidim dali she stane" yes, "open youtube" no."""
    text = (text or "").lower()
    if _CYRILLIC.search(text):
        return False
    tokens = _LATIN_WORD.findall(text)
    if len(tokens) < 2:
        return False
    strong = sum(t in _STRONG_LATIN and t not in _WEAK for t in tokens)
    score = strong + 0.5 * sum(t in _WEAK for t in tokens)
    english = sum(t in _ENGLISH for t in tokens)
    return strong >= 1 and score >= 1.0 and english < score and english <= len(tokens) / 4


def to_cyrillic(text: str) -> str:
    """A rough Cyrillic spelling of Bulgarian typed in Latin letters ("pusni muzika" -> "пусни музика").
    Names and sites (jarvis, youtube, .com) stay. Only a hint for the model: a letter can be ambiguous."""

    def word(m: re.Match) -> str:
        w = m.group(0)
        if _KEEP.match(w):
            return w
        out = _TO_CYR_RX.sub(lambda x: _TO_CYR_MAP[x.group(0).lower()], w)
        return out.capitalize() if w[:1].isupper() else out

    return re.sub(r"[A-Za-z0-9][A-Za-z0-9\-.:/]*", word, text or "")

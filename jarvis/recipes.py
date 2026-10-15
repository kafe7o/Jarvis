"""Recipes: how Jarvis did something that worked, kept for next time.

A task that went wrong first and then succeeded (or one the owner praised) is worth more than a one-line
lesson: the way that worked (which page, button, tool, words) is written down as a recipe and shown to the
brain again whenever a similar request comes, so Jarvis does it right and fast at once instead of learning
the same thing task after task. Recipes live in the store (table ``recipes``) and are matched by the words of
the request; LESSONS.md stays for the short rules.
"""
from __future__ import annotations

import json
import re

from . import router

WORD = re.compile(r"[a-zа-я0-9][a-zа-я0-9_.-]{2,}", re.I)
# Words that say nothing about what a task is (the request's grammar), so they never match a recipe.
STOP = set("""това тези този тази които което който какво като след преди през върху между сега пак още вече само
също много малко добре моля искам иска искаш може можеш трябва нека дай ми ме го ги му им си се ли не да и или
ама но защото понеже когато където докато ако така тук там всички всичко нещо някой някакъв един една едно
един едни тях него нея мен теб нас вас the and for with from into that this then when what some your
jarvis джарвис готин моля бързо веднага after before please""".split())
LIMIT = 2  # recipes shown with one request
# Words in every task (actions and the screen), so they never say two tasks are alike: "отвори" and "прозореца"
# are in a Task Manager request and in a NotebookLM recipe both.
GENERIC = set("""отвори отваряне отваря отварям натисни натискане натиска кликни кликване клик провери проверка проверява
напиши написване въведи въвеждане избери избор пусни пускане стартирай стартиране затвори затваряне запази
запазване свали изтегли намери търси търсене виж гледай прочети чете прочитане покажи сложи направи прави работи
използвай ползвай изчакай изчакване повтори опитай избягвай първо после накрая отново пак
прозореца прозорец прозорци екрана екран бутон бутона бутони поле полето полета страницата страница меню менюто
папка папката файл файла файлът файлове стъпка стъпки резултат резултата път пътя име името адрес адреса текст
текста програмата програма приложението приложение приложенията лаптопа компютъра компютър браузъра браузър таб
раздел курсор мишката клавиатурата клавиш клавиша секунди минути
open opens click press type check select find read show window screen button page file files app program
step steps result wait again then first""".split())
MAX_RECIPES = 300
PROMPT = """The owner asked: {request}
The steps you took, in order (tool, arguments -> result):
{steps}
How it ended: {answer}

The task went wrong first and then worked. Write a recipe for next time, so you do it right and fast at once.
Line 1: a short title in Bulgarian saying what the recipe is for (the kind of task and the program or site), \
e.g. "Качване на файлове в NotebookLM през браузъра".
Then 3 to 8 numbered steps in Bulgarian: exactly what worked (which tool, page, button, words typed), and one \
last line "Избягвай: ..." with what wasted time. Only what will hold next time: no names, passwords, dates or \
exact file names. If nothing here is reusable, or the task did not really succeed, write exactly: NONE"""


ENDINGS = ("ането", "енето", "ането", "ане", "ене", "ние", "ове", "ите", "ата", "ото", "ете", "ият", "ия", "ът", "ят",
           "та", "то", "те", "а", "я", "и", "е", "о", "у")  # the longest first; stripped once, for the stem


def words(text: str) -> set[str]:
    """The words of a request or a recipe that say what it is about, without the grammar. A request typed in
    Latin letters gives both spellings, so "kachi" finds "качи" and "notebooklm" stays as it is."""
    text = text or ""
    if router.latin_bulgarian(text):
        text += " " + router.to_cyrillic(text)
    return {w.lower().strip(".-_") for w in WORD.findall(text) if w.lower() not in STOP and len(w) > 2}


def stem(word: str) -> str:
    """"качване", "качи" -> "кач"; "файлове", "файла" -> "файл": enough of a word to tell it is the same one."""
    if word.isascii():
        return word
    for end in ENDINGS:
        if word.endswith(end) and len(word) - len(end) >= 3:
            word = word[:-len(end)]
            break
    return word[:5]


def same(a: str, b: str) -> bool:
    x, y = stem(a), stem(b)
    return x == y or (len(min(x, y, key=len)) >= 3 and (x.startswith(y) or y.startswith(x)))


def overlap(asked: set[str], known: set[str]) -> int:
    """How many of the asked words a recipe's words also say."""
    return sum(1 for a in asked if any(same(a, k) for k in known))


def worth_it(steps: list, answer: str) -> bool:
    """A task that struggled and then came through: some step failed, the last one did not, and the answer says
    it is done. A task that went smoothly teaches nothing new; one that failed gets a lesson, not a recipe."""
    from .brain import claims_done

    return (len(steps) >= 3 and any(err for *_r, err in steps) and not steps[-1][3] and claims_done(answer))


def describe(steps: list, limit: int = 40) -> str:
    return "\n".join(f"- {name} {json.dumps(args, ensure_ascii=False)[:150]} -> {'FAILED' if err else 'OK'}: "
                     f"{(out or '')[:150]}" for name, args, out, err in steps[-limit:])


def parse(said: str) -> tuple[str, str] | None:
    """The brain's recipe: (title, steps); None for NONE or something too short to be one."""
    lines = [line.strip(" -*#\"") for line in (said or "").splitlines() if line.strip()]
    if not lines or lines[0].upper().startswith("NONE") or len(lines) < 3:
        return None
    return lines[0][:120], "\n".join(lines[1:])[:1500]


def save(store, title: str, steps: str) -> bool:
    """Keep a recipe; one with the same title is replaced by the newer way. The oldest go when there are too many."""
    key = " ".join(sorted(words(title)))
    if not key:
        return False
    vocabulary = " ".join(sorted(words(title) | words(steps)))
    same = store.query("SELECT id, uses FROM recipes WHERE title_key=?", (key,))
    if same:
        store.execute("UPDATE recipes SET title=?, words=?, steps=? WHERE id=?", (title, vocabulary, steps, same[0]["id"]))
        return True
    store.insert("recipes", title=title, title_key=key, words=vocabulary, steps=steps, uses=0)
    extra = store.query("SELECT id FROM recipes ORDER BY uses DESC, id DESC LIMIT -1 OFFSET ?", (MAX_RECIPES,))
    for row in extra:
        store.execute("DELETE FROM recipes WHERE id=?", (row["id"],))
    return True


def matching(store, text: str, limit: int = LIMIT) -> list[dict]:
    """The recipes about what ``text`` asks: at least two telling words in common, the best matches first."""
    asked = words(text) - GENERIC
    if not asked:
        return []
    found = []
    for row in store.query("SELECT id, title, words, steps, uses FROM recipes"):
        title = words(row["title"]) - GENERIC
        in_steps = set(row["words"].split()) - GENERIC - title
        in_title, common = overlap(asked, title), overlap(asked, in_steps)
        # the title says what the recipe is for: two of its words, all of a short one, or one and two of the steps
        if in_title >= 2 or (in_title and in_title == len(title)) or (in_title and common >= 2):
            found.append((in_title * 2 + common, row["uses"], row))
    found.sort(key=lambda f: (f[0], f[1]), reverse=True)
    chosen = [row for *_s, row in found[:limit]]
    for row in chosen:
        store.execute("UPDATE recipes SET uses = uses + 1 WHERE id=?", (row["id"],))
    return chosen


def hint(found: list[dict]) -> str:
    """The note that goes with a request the recipes are about (for the brain, not the saved conversation)."""
    if not found:
        return ""
    body = "\n\n".join(f"• {r['title']}\n{r['steps']}" for r in found)
    return ("\n\n(Recipes from earlier tasks of yours that worked and may be like this one. Use a recipe only when "
            "it is about the same kind of task as this request, as the way to do it; if none fits, ignore them. "
            "Never mention them:\n" + body + ")")


def listing(store, limit: int = 20) -> str:
    rows = store.query("SELECT title, uses FROM recipes ORDER BY uses DESC, id DESC LIMIT ?", (limit,))
    return "\n".join(f"- {r['title']} (used {r['uses']}x)" for r in rows) or "(none yet)"

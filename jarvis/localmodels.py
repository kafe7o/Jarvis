"""The models Jarvis thinks with, downloaded onto the laptop by himself (Settings > Мозък, or "изтегли Qwen 9B" in the chat).

Ollama runs them. It pulls a model from its own library or straight from Hugging Face as a GGUF file
("hf.co/<user>/<repo>:<quant>"), and Jarvis shows the progress. Only light models are offered: the owner's laptop
has an RTX 4050 with 6 GB of video memory and 16 GB of RAM, so a 4B model fits in the graphics card and a 9B one
runs partly from RAM (about 10-15 words a second).

Qwen 3.5 9B Abliterated is the one the owner asked for by name: Qwen 3.5 9B with its refusals removed. Jarvis's own
confirmations (calls, messages, payments) are code, so they stay whatever model answers.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from . import local

log = logging.getLogger("jarvis.localmodels")

WINDOWS = sys.platform.startswith("win")
NO_WINDOW = 0x08000000 if WINDOWS else 0
OLLAMA_SETUP = "https://ollama.com/download/OllamaSetup.exe"
# Ollama imports a Qwen 3.5 GGUF from Hugging Face only from this version (its llama.cpp rebase); its own library
# models work from 0.17.7.
HF_GGUF_FROM = (0, 30, 0)

# The light models offered, best first. "smart" thinks on the hard steps, "quick" takes the easy ones when the
# smart one is the brain (local.quick_partner), "memory" finds remembered facts by meaning.
CATALOG = [
    {"id": "huihui_ai/qwen3.5-abliterated:9b", "name": "Qwen 3.5 9B Abliterated", "gb": 6.6, "role": "smart",
     "about": "умният: без откази, вижда снимки, ползва инструменти и мисли на трудните стъпки; на 6 GB видеокарта "
              "върви частично от RAM, около 10-15 думи в секунда"},
    {"id": "huihui_ai/qwen3.5-abliterated:4B", "name": "Qwen 3.5 4B Abliterated", "gb": 3.3, "role": "quick",
     "about": "бързият: цял във видеокартата; с него лесните стъпки вървят на 4B, а трудните на 9B"},
    {"id": "hf.co/mradermacher/Huihui-Qwen3.5-9B-abliterated-i1-GGUF:Q4_K_S", "name": "Qwen 3.5 9B Abliterated GGUF",
     "gb": 5.4, "role": "smart", "hf": True,
     "about": "същият 9B като GGUF файл направо от Hugging Face (i1-Q4_K_S, по-малък); иска Ollama 0.30 или по-нова"},
    {"id": "qwen3-embedding:0.6b", "name": "Памет по смисъл", "gb": 0.6, "role": "memory",
     "about": "малък модел, с който Jarvis намира запомненото по смисъл, а не само по думи (и на български)"},
]

_pulls: dict[str, dict] = {}  # model -> {"status", "percent", "done", "error"} while and after it downloads
_lock = threading.Lock()


def _call(path: str, body: dict | None = None, method: str = "POST", timeout: float = 10) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(local.url() + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw) if raw.strip() else {}


def version() -> str:
    """Ollama's version ("" when it is not running)."""
    try:
        return str(_call("/api/version", method="GET", timeout=1.5).get("version", ""))
    except Exception:
        return ""


def version_tuple(text: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3]) or (0,)


def installed() -> list[dict]:
    """The models Ollama has, with their size in GB ([] when it is not running)."""
    try:
        found = _call("/api/tags", method="GET", timeout=2).get("models", [])
    except Exception:
        return []
    return [{"id": m.get("name", ""), "gb": round((m.get("size") or 0) / 1e9, 1)} for m in found]


def ollama_program() -> str | None:
    """Where ollama.exe is on this computer (None when Ollama is not installed)."""
    found = shutil.which("ollama")
    if found:
        return found
    base = os.environ.get("LOCALAPPDATA")
    exe = Path(base) / "Programs" / "Ollama" / "ollama.exe" if base else None
    return str(exe) if exe and exe.exists() else None


# Ollama's own settings that make more of a model fit a small graphics card: flash attention, and the model's
# working memory of the conversation (the KV cache, about 1 GB for 32,000 tokens on a 9B) kept in 8 bits instead of
# 16, which halves it at almost no cost to quality; and one conversation at a time (Ollama otherwise keeps room for
# several, each with its own working memory).
TUNING = {"OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0", "OLLAMA_NUM_PARALLEL": "1"}


def tune(run=None) -> list[str]:
    """Save TUNING as the user's own settings on Windows (where Ollama's tray app reads them when it starts), the
    ones not set yet; the names saved. Elsewhere only the server Jarvis starts gets them."""
    missing = [k for k in TUNING if not os.environ.get(k)]
    if not WINDOWS or not missing:
        return []
    for key in missing:
        (run or subprocess.run)(["setx", key, TUNING[key]], capture_output=True, creationflags=NO_WINDOW)
        os.environ[key] = TUNING[key]
    return missing


def start_ollama() -> bool:
    """Start Ollama's server in the background when it is installed but not running. True when it answers."""
    if version():
        return True
    exe = ollama_program()
    if not exe:
        return False
    try:
        tune()
    except Exception as exc:
        log.info("could not save Ollama's settings: %s", exc)
    env = {**TUNING, **os.environ}
    subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW,
                     env=env)
    for _ in range(20):
        time.sleep(0.5)
        if version():
            return True
    return False


_hardware: dict = {}


def hardware() -> dict:
    """The graphics card (NVIDIA), its memory, the RAM and the free disk in GB; 0 where it cannot be told. Asked
    once a minute at most (the Мозък tab asks every 2 s while a model downloads)."""
    if _hardware and time.time() - _hardware["at"] < 60:
        return _hardware["value"]
    value = _measure()
    _hardware.update(at=time.time(), value=value)
    return value


def _measure() -> dict:
    gpu, vram = "", 0.0
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=8, creationflags=NO_WINDOW).stdout
        name, _, mib = out.strip().splitlines()[0].rpartition(",")
        gpu, vram = name.strip(), round(float(mib) / 1024, 1)
    except Exception:
        pass
    return {"gpu": gpu, "vram": vram, "ram": round(ram_bytes() / 2**30, 1),
            "disk": round(shutil.disk_usage(Path.home()).free / 1e9)}


def ram_bytes() -> int:
    try:
        import psutil

        return int(psutil.virtual_memory().total)
    except Exception:
        pass
    if WINDOWS:
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                        ("free", ctypes.c_ulonglong), ("page", ctypes.c_ulonglong), ("page_free", ctypes.c_ulonglong),
                        ("virtual", ctypes.c_ulonglong), ("virtual_free", ctypes.c_ulonglong),
                        ("extended", ctypes.c_ulonglong)]

        status = Status()
        status.length = ctypes.sizeof(Status)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return int(status.total)
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 0


def fit(gb: float, hw: dict) -> str:
    """How a model of ``gb`` runs on this hardware, in words for the owner."""
    vram, ram = hw.get("vram") or 0, hw.get("ram") or 0
    if vram and gb + 0.7 <= vram:
        return "цял във видеокартата: бърз"
    if vram and gb <= vram + ram / 2 - 1:
        return "част във видеокартата, част в RAM: върви, по-бавно"
    if not vram and gb <= ram / 2:
        return "без видеокарта, на процесора: бавен"
    return "твърде голям за този компютър"


def pull(model: str) -> str:
    """Start downloading ``model`` in the background (Ollama's library name or hf.co/<user>/<repo>:<quant>).
    Its progress is in progress()."""
    model = model.strip()
    if not re.fullmatch(r"[\w.\-/:]+", model):
        raise ValueError(f"„{model}“ не е име на модел.")
    if not version() and not start_ollama():
        raise RuntimeError("Ollama не е инсталиран или не е пуснат. Натисни „Инсталирай Ollama“ в Настройки > Мозък.")
    if model.startswith("hf.co/") and version_tuple(version()) < HF_GGUF_FROM:
        raise RuntimeError(f"Ollama {version()} е стар за GGUF от Hugging Face: обнови Ollama (Настройки > Мозък > "
                           "„Инсталирай Ollama“ слага най-новия) или избери същия модел от библиотеката на Ollama.")
    with _lock:
        if model in _pulls and not _pulls[model]["done"]:
            return f"{model} вече се тегли ({_pulls[model]['percent']}%)."
        _pulls[model] = {"status": "започвам", "percent": 0, "done": False, "error": ""}
    threading.Thread(target=_pull, args=(model,), name="jarvis-pull", daemon=True).start()
    return f"Започнах да тегля {model}."


def _pull(model: str, post=None) -> None:
    state = _pulls[model]
    sizes: dict[str, list] = {}  # layer -> [done, total]
    try:
        lines = (post or _stream)("/api/pull", {"model": model, "stream": True})
        for line in lines:
            step = json.loads(line) if isinstance(line, (str, bytes)) else line
            if step.get("error"):
                raise RuntimeError(step["error"])
            if step.get("total"):
                sizes[step.get("digest", "")] = [step.get("completed") or 0, step["total"]]
                done, total = (sum(v[0] for v in sizes.values()), sum(v[1] for v in sizes.values()))
                state["percent"] = int(done * 100 / total) if total else 0
                state["status"] = f"{done / 1e9:.1f} от {total / 1e9:.1f} GB"
            elif step.get("status"):
                state["status"] = step["status"]
        state.update(percent=100, status="готово")
        local._seen["at"] = 0  # the new model is seen at once
    except Exception as exc:
        log.warning("pulling %s failed: %s", model, exc)
        state["error"] = str(exc)
    finally:
        state["done"] = True


def _stream(path: str, body: dict):
    req = urllib.request.Request(local.url() + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3600) as resp:
        for line in resp:
            if line.strip():
                yield line


def progress() -> dict:
    return {name: dict(state) for name, state in _pulls.items()}


def delete(model: str) -> str:
    _call("/api/delete", {"model": model}, method="DELETE")
    local._seen["at"] = 0
    return f"Изтрих {model}."


def resolve(text: str) -> str | None:
    """The model meant by words like "qwen 9b", "4b", "hugging face 9b", "памет" (a full name is taken as it is)."""
    text = (text or "").strip()
    if re.fullmatch(r"(hf\.co/)?[\w.\-]+/[\w.\-]+(:[\w.\-]+)?|[\w.\-]+:[\w.\-]+", text):
        return text
    low = text.lower()
    if any(w in low for w in ("памет", "pamet", "embed", "смисъл")):
        return CATALOG[3]["id"]
    if "4b" in low or "4 b" in low or "бърз" in low:
        return CATALOG[1]["id"]
    if any(w in low for w in ("hugging", "хъгинг", "gguf", "гуф", "guff", "hf")):
        return CATALOG[2]["id"]
    if any(w in low for w in ("9b", "9 b", "qwen", "куен", "abliter", "умн")):
        return CATALOG[0]["id"]
    return None


# A Qwen 3.5 GGUF made by someone else: Ollama's own way of writing its prompts and reading its tool calls (the
# template inside such a file prints the calls as text), with Qwen's settings for agent work. An older Ollama that
# does not know RENDERER/PARSER gets the file's own template instead (import_gguf).
QWEN35 = ("TEMPLATE {{ .Prompt }}\nRENDERER qwen3.5\nPARSER qwen3.5\n")
SETTINGS = (f"PARAMETER num_ctx {local.NUM_CTX}\nPARAMETER temperature 0.6\nPARAMETER top_p 0.95\nPARAMETER top_k 20\n"
            "PARAMETER min_p 0\n")


def models_folder(make: bool = True) -> Path:
    """The owner's own folder for model files (Documents\\Jarvis Models, or JARVIS_MODELS_DIR): a downloaded .gguf
    goes here, so the models are in one place instead of mixed with everything else in Downloads."""
    folder = Path(os.environ.get("JARVIS_MODELS_DIR") or Path.home() / "Documents" / "Jarvis Models").expanduser()
    inside = Path.home() / "Documents" / "Jarvis" / "Jarvis Models"  # made by hand inside Jarvis's own folder
    if not os.environ.get("JARVIS_MODELS_DIR") and not folder.is_dir() and inside.is_dir():
        return inside
    if make:
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return folder


def open_folder() -> str:
    """Show the models folder in Explorer (the owner drags the downloaded files in)."""
    folder = models_folder()
    if WINDOWS:
        os.startfile(folder)  # noqa: S606 (a folder, opened in Explorer)
    return str(folder)


def gguf_files(folders: list[Path] | None = None) -> list[dict]:
    """The model files (.gguf) in the models folder, Downloads, on the desktop and in Documents, biggest first. A
    vision add-on (mmproj) and anything under 300 MB are not a model."""
    home = Path.home()
    folders = folders or [models_folder(make=False), home / "Downloads", home / "Desktop", home / "Documents",
                          home / "OneDrive" / "Desktop"]
    found = {}
    for folder in folders:
        try:
            # two folders deep at most: Documents also holds Jarvis's own code with thousands of files
            paths = [p for pattern in ("*.gguf", "*/*.gguf", "*/*/*.gguf") for p in folder.glob(pattern)]
            for path in paths if folder.is_dir() else ():
                size = path.stat().st_size
                if "mmproj" not in path.name.lower() and size > 300e6:
                    found[str(path)] = {"path": str(path), "name": model_tag(path.name), "gb": round(size / 1e9, 1)}
        except OSError:
            continue
    return sorted(found.values(), key=lambda f: -f["gb"])


def model_tag(filename: str) -> str:
    """The name a file gets in Ollama: "Qwen3.5-9B-abliterated-Q4_K_M.gguf" -> "qwen3.5-9b-abliterated-q4_k_m"."""
    stem = re.sub(r"\.gguf$", "", filename, flags=re.I).lower()
    return re.sub(r"[^a-z0-9._-]+", "-", stem).strip("-._")[:80] or "model"


def architecture(path) -> str:
    """The model family written inside a .gguf file ("qwen35", "gemma3", ...), "" if it cannot be read."""
    try:
        with open(path, "rb") as f:
            head = f.read(1 << 16)
    except OSError:
        return ""
    found = re.search(rb"general\.architecture\x08\x00\x00\x00(.{8})", head, re.S)
    if not head.startswith(b"GGUF") or not found:
        return ""
    size = int.from_bytes(found.group(1), "little")
    return head[found.end():found.end() + size].decode("utf-8", "replace") if size < 64 else ""


def is_qwen35(path) -> bool:
    """A Qwen 3.5 model, by its file name or by the family inside the file (Ornith 1.5 is one under another name)."""
    return bool(re.search(r"qwen[-_ ]?3[._]?5", Path(path).name, re.I)) or architecture(path).startswith("qwen35")


def modelfile(path: str, renderer: bool = True) -> str:
    head = f"FROM {path}\n"
    return head + (QWEN35 if renderer and is_qwen35(path) else "") + SETTINGS


def import_gguf(path: str, run=None) -> str:
    """Add a .gguf file from this computer to Ollama under its own name, in the background (Ollama copies it, a
    minute or two for 5 GB). Its progress is in progress() under that name."""
    file = Path(path)
    if not file.is_file() or file.suffix.lower() != ".gguf":
        raise ValueError(f"Няма такъв .gguf файл: {path}")
    gb, hw = round(file.stat().st_size / 1e9, 1), hardware()
    if hw.get("ram") and fit(gb, hw) == "твърде голям за този компютър":
        raise ValueError(f"{file.name} е {gb} GB: не се побира в паметта на този компютър. Изтегли го като Q4_K_M "
                         "(около 5-6 GB за 9B), F16/BF16 файловете са за много по-големи видеокарти.")
    exe = ollama_program()
    if not exe or (not version() and not start_ollama()):
        raise RuntimeError("Ollama не е инсталиран или не е пуснат. Натисни „Инсталирай Ollama“ в Настройки > Мозък.")
    name = model_tag(file.name)
    with _lock:
        if name in _pulls and not _pulls[name]["done"]:
            return f"{name} вече се добавя."
        _pulls[name] = {"status": "добавям файла в Ollama (копира го)", "percent": 0, "done": False, "error": ""}

    def go():
        state = _pulls[name]
        spec = Path.home() / ".jarvis" / f"{name}.Modelfile"
        try:
            spec.parent.mkdir(parents=True, exist_ok=True)
            error = ""
            for renderer in (True, False):  # an older Ollama does not know RENDERER/PARSER: the file's template
                spec.write_text(modelfile(str(file), renderer), encoding="utf-8")
                done = (run or subprocess.run)([exe, "create", name, "-f", str(spec)], capture_output=True, text=True,
                                               encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
                if done.returncode == 0:
                    break
                error = (done.stderr or done.stdout or "").strip()[-400:]
                if not (renderer and is_qwen35(file)):
                    raise RuntimeError(error or "ollama create failed")
            else:
                raise RuntimeError(error or "ollama create failed")
            state.update(percent=100, status="готово")
            local._seen["at"] = 0
        except Exception as exc:
            log.warning("importing %s failed: %s", file, exc)
            state["error"] = str(exc)
            if "unknown model architecture" in str(exc) or "qwen35" in str(exc):
                state["error"] += " (обнови Ollama: Настройки > Мозък > „Инсталирай Ollama“)"
        finally:
            state["done"] = True

    threading.Thread(target=go, name="jarvis-import", daemon=True).start()
    return f"Добавям {file.name} в Ollama като {name}."


def friendly(model_id: str) -> str:
    """A short name for the slider: "ornith-1.5-9b-q4_k_m:latest" -> "Ornith 1.5 9B"."""
    known = next((m["name"] for m in CATALOG if m["id"] == model_id or m["id"] + ":latest" == model_id), None)
    if known:
        return known
    stem = model_id.split("/")[-1].removesuffix(":latest").replace(":", " ")
    stem = re.sub(r"[-_ .](i?q\d[\w]*|f16|bf16|f32|gguf)$", "", stem, flags=re.I)
    stem = re.sub(r"[-_ ](it|instruct)$", "", stem, flags=re.I)
    words = [w.upper() if re.fullmatch(r"\d+(\.\d+)?[bm]|e\d+b|a\d+b", w, re.I) else w[:1].upper() + w[1:]
             for w in re.split(r"[-_ ]+", stem) if w]
    return " ".join(words)[:40] or model_id


def scale(local_model: str = "") -> dict:
    """The brain slider: the models on this laptop from the fastest (smallest) to the smartest (biggest), the one in
    use, and whether Авто is on (no model fixed: Jarvis picks one for every step, local.choose)."""
    hw = hardware()
    have = sorted((m for m in installed() if "embed" not in m["id"].lower()), key=lambda m: m["gb"])
    best = local.pick() if have else None
    stops = [{"value": m["id"], "name": friendly(m["id"]), "model": m["id"], "gb": m["gb"],
              "about": f"{m['gb']} GB, {fit(m['gb'], hw)}", "recommended": m["id"] == best} for m in have]
    wanted = (local_model or "").strip()
    at = next((i for i, s in enumerate(stops) if wanted and (s["value"] == wanted or s["value"].split(":")[0] == wanted)), -1)
    return {"stops": stops, "at": at, "model": wanted or best or "", "auto": not wanted}


def overview(local_model: str = "") -> dict:
    """Everything Settings > Мозък shows: Ollama, the hardware, the offered models and the installed ones."""
    hw = hardware()
    have = installed()
    names = {m["id"] for m in have}
    offered = []
    for item in CATALOG:
        entry = dict(item, fit=fit(item["gb"], hw), installed=item["id"] in names or item["id"] + ":latest" in names)
        offered.append(entry)
    known = {i["id"] for i in CATALOG}
    others = [dict(m, fit=fit(m["gb"], hw)) for m in have if m["id"] not in known]
    chosen = local.pick(local_model or None) if names else None
    files = [dict(f, fit=fit(f["gb"], hw)) for f in gguf_files()
             if f["name"] not in names and f["name"] + ":latest" not in names]
    return {"ollama": version(), "installed_ollama": bool(ollama_program()), "hardware": hw, "models": offered,
            "folder": str(models_folder()),
            "others": others, "files": files, "chosen": chosen or "", "quick": local.quick_partner(chosen) if chosen and not local_model else None,
            "pulls": progress(), "setup_url": OLLAMA_SETUP, "scale": scale(local_model)}


def install_ollama(fetch=None, run=None) -> str:
    """Download Ollama's installer and start it (it shows its own progress, no questions); Ollama starts by itself
    after it. Windows only."""
    if not WINDOWS:
        return "На този компютър сложи Ollama от https://ollama.com/download"
    target = Path.home() / ".jarvis" / "OllamaSetup.exe"
    target.parent.mkdir(parents=True, exist_ok=True)

    def go():
        state = _pulls["ollama"]
        try:
            (fetch or _download)(OLLAMA_SETUP, target, state)
            state["status"] = "инсталирам"
            tune(run)
            (run or subprocess.run)([str(target), "/SILENT"], check=False)
            state.update(percent=100, status="готово")
        except Exception as exc:
            state["error"] = str(exc)
        finally:
            state["done"] = True

    _pulls["ollama"] = {"status": "тегля", "percent": 0, "done": False, "error": ""}
    threading.Thread(target=go, name="jarvis-ollama-setup", daemon=True).start()
    return "Тегля Ollama и после го инсталирам."


def _download(link: str, target: Path, state: dict) -> None:
    with urllib.request.urlopen(link, timeout=60) as resp, open(target, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if total:
                state["percent"] = int(done * 100 / total)
                state["status"] = f"{done / 1e9:.1f} от {total / 1e9:.1f} GB"

"""The owner's own settings by asking: the voice Jarvis speaks with ("смени гласа") and the models on the laptop
he thinks with ("изтегли Qwen 9B", "мисли с 4B"). Only the owner can change them; they are saved in .env.
"""

from __future__ import annotations

import os

from .. import tts
from ..tools import ToolRegistry, obj


def register(registry: ToolRegistry, ctx) -> None:
    @registry.tool(
        "Change the voice Jarvis speaks with in the app, when the owner asks: 'смени гласа', 'сложи женски глас', "
        "'гласа на Калина'. Pass the voice's name, or 'друг' for the next one, or 'мъжки'/'женски'. Voices: "
        + "; ".join(f"{n} ({a})" for _v, n, a in tts.VOICES)
        + ". Repeat the answer as it is: it names the voice really saved.",
        obj({"voice": ("string", "A voice name from the list, 'друг', 'мъжки' or 'женски'")}),
    )
    def switch_voice(voice: str = ""):
        from ..brain import TURN
        from . import save_settings

        user = (TURN.get() or {}).get("user")
        if user is not None and not user.is_owner:
            raise PermissionError("Only the owner can change Jarvis's voice.")
        found = tts.find_voice(voice, ctx.settings.tts_voice)
        if found is None:
            raise ValueError(f"Няма глас „{voice}“. Има: " + ", ".join(n for _v, n, _a in tts.VOICES)
                             + ". Може да ги чуеш в Настройки > Глас.")
        vid, name, about = found
        save_settings(ctx, {"JARVIS_TTS_VOICE": vid})
        ctx.settings.tts_voice = vid
        hub = getattr(ctx, "hub", None)
        if hub is not None:
            hub.events.add("voice", voice=vid, name=name)
        note = " Докато има ElevenLabs ключ, говори ElevenLabs." if os.environ.get("ELEVENLABS_API_KEY") else ""
        return f"Вече говоря с гласа {name} ({about}).{note} Всички гласове можеш да чуеш в Настройки > Глас."

    @registry.tool(
        "The models you think with, on this laptop (Ollama), as in Settings > Мозък. Actions: list (installed "
        "models, downloads in progress, .gguf files in the models folder Documents\\Jarvis Models and in Downloads, "
        "the graphics card and RAM); download (the owner's Qwen 3.5 9B Abliterated, its fast 4B, or any Ollama or "
        "Hugging Face name such as hf.co/user/repo:Q4_K_M); add_file (a .gguf file already on the computer; empty = "
        "the biggest one found); use (think with it from the next step on); delete; install_ollama. A download runs "
        "in the background: say it started, and list shows its progress.",
        obj({"action": ("string", "list, download, add_file, use, delete or install_ollama"),
             "model?": ("string", "A model name or words like 'qwen 9b', '4b', 'памет'; for add_file a file path")}),
    )
    def local_models(action: str = "list", model: str = ""):
        from .. import localmodels
        from ..brain import TURN
        from . import save_settings

        user = (TURN.get() or {}).get("user")
        if user is not None and not user.is_owner:
            raise PermissionError("Only the owner manages the brains on the laptop.")
        action = (action or "list").strip().lower()
        if action == "install_ollama":
            return localmodels.install_ollama()
        if action == "add_file":
            files = localmodels.gguf_files()
            path = model.strip() or (files[0]["path"] if files else "")
            if not path:
                return "There is no .gguf file in Documents\\Jarvis Models, Downloads, the desktop or Documents."
            return localmodels.import_gguf(path)
        if action in ("download", "use", "delete"):
            name = localmodels.resolve(model)
            if not name:
                return ("Which model? The light ones: " + "; ".join(f"{m['name']} ({m['id']}, {m['gb']} GB)"
                                                                      for m in localmodels.CATALOG))
            if action == "download":
                return localmodels.pull(name)
            if action == "delete":
                return localmodels.delete(name)
            installed = [m["id"] for m in localmodels.installed()]
            name = next((n for n in installed if n == name or n.split(":")[0] == name.split(":")[0]), None)
            if not name:
                return f"{model} is not on the laptop yet: download it (or add_file) first."
            save_settings(ctx, {"JARVIS_LOCAL_MODEL": name})
            ctx.settings.local_model = name
            return f"From the next step on you think with {name}."
        view = localmodels.overview(ctx.settings.local_model)
        hw = view["hardware"]
        lines = [f"Ollama: {view['ollama'] or ('installed, not running' if view['installed_ollama'] else 'not installed')}",
                 f"Hardware: {hw['gpu'] or 'no NVIDIA card found'} {hw['vram']} GB, RAM {hw['ram']} GB, free disk {hw['disk']} GB",
                 f"Thinking with: {view['chosen'] or 'none'}" + (f" (easy steps on {view['quick']})" if view["quick"] else "")]
        lines += [f"{'✅' if m['installed'] else '⬇'} {m['name']} — {m['id']}, {m['gb']} GB, {m['fit']}" for m in view["models"]]
        lines += [f"✅ {m['id']}, {m['gb']} GB" for m in view["others"]]
        lines += [f"📄 {f['path']} ({f['gb']} GB) — not added yet (add_file)" for f in view["files"]]
        lines += [f"… {n}: {st['error'] or st['status']} ({st['percent']}%)" for n, st in view["pulls"].items()]
        return "\n".join(lines)

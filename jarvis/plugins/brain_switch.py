"""Switch Jarvis's brain (the Claude model) by asking: "switch to Fable", "go faster".

Only the owner can switch; the choice is saved in .env and the app changes colour to match.
"""

from __future__ import annotations

import os

from ..tools import ToolRegistry, obj

# key: (model id, name, what it is for)
BRAINS = {
    "opus": ("claude-opus-5-5", "Opus 5.5", "the default: strong and quick"),
    "fable": ("claude-fable-5-1", "Fable 5.1", "the most capable, for the hardest problems; slower and pricier"),
    "sonnet": ("claude-sonnet-5-5", "Sonnet 5.5", "fast everyday work, cheaper"),
    "haiku": ("claude-haiku-5-5", "Haiku 5.5", "the fastest and cheapest, for simple things"),
}


def brain_key(model: str) -> str:
    return next((key for key, (mid, _n, _d) in BRAINS.items() if mid == model), "custom")


def register(registry: ToolRegistry, ctx) -> None:
    @registry.tool(
        "Switch your own brain (the Claude model you run on) when the owner asks, e.g. 'switch to Fable', "
        "'use the fastest brain'. Options: " + "; ".join(f"{k} = {n}: {d}" for k, (_m, n, d) in BRAINS.items()),
        obj({"brain": ("string", "One of: " + ", ".join(BRAINS))}),
    )
    def switch_brain(brain: str):
        from ..brain import TURN

        user = (TURN.get() or {}).get("user")
        if user is not None and not user.is_owner:
            raise PermissionError("Only the owner can switch Jarvis's brain.")
        key = brain.strip().lower()
        if key not in BRAINS:
            raise ValueError(f"Unknown brain '{brain}'. Choose: {', '.join(BRAINS)}")
        model, name, _desc = BRAINS[key]
        hub = getattr(ctx, "hub", None)
        if hub is not None:
            hub.save_settings({"JARVIS_MODEL": model})
            hub.events.add("brain", brain=key, name=name)
        os.environ["JARVIS_MODEL"] = model
        ctx.settings.model = model
        return f"Switched to {name} ({model}). Your next answer comes from it; say so in one short line, in character."

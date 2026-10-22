"""Watching a video on this computer, with no AI service: frames taken from it (PyAV, which Whisper already brings)
laid out on one picture the brain looks at, and what is said in it written out by Whisper on this computer. A link
(YouTube, TikTok, Instagram...) is downloaded first with yt-dlp."""

from __future__ import annotations

import io
import logging
from pathlib import Path

from ..tools import Image, ToolRegistry, obj

log = logging.getLogger("jarvis.video")

FRAMES = 12  # 4 across, 3 down: enough to follow a short clip, small enough for a laptop brain to read
ACROSS = 4
THUMB = 320  # pixels wide, each frame
LONGEST_SPEECH = 30 * 60  # seconds of video whose speech is written out; a longer one gets its pictures only


def length(container) -> float:
    import av

    if container.duration:
        return container.duration / av.time_base
    stream = container.streams.video[0]
    return float(stream.duration * stream.time_base) if stream.duration else 0.0


def frames(path: str, count: int = FRAMES) -> tuple[bytes, float, int]:
    """One JPEG with ``count`` frames spread evenly over the video, each with its time; also the video's length in
    seconds and how many frames it holds."""
    import av
    from PIL import Image as Picture
    from PIL import ImageDraw

    shots = []
    with av.open(path) as container:
        stream = container.streams.video[0]
        seconds = length(container)
        for i in range(count):
            at = seconds * (i + 0.5) / count
            try:
                container.seek(int(at / stream.time_base), stream=stream)  # the key frame just before ``at``
                for n, frame in enumerate(container.decode(stream)):
                    if frame.time is None or frame.time >= at - 0.05 or n > 90:
                        shots.append((at, frame.to_image()))
                        break
            except (av.error.FFmpegError, OSError) as exc:  # a broken stretch: the other frames still come
                log.info("no frame at %.1f s of %s: %s", at, path, exc)
    if not shots:
        raise RuntimeError("Не можах да извадя кадри от видеото.")
    height = round(THUMB * shots[0][1].height / max(1, shots[0][1].width))
    rows = -(-len(shots) // ACROSS)
    sheet = Picture.new("RGB", (THUMB * min(ACROSS, len(shots)), height * rows), "black")
    draw = ImageDraw.Draw(sheet)
    for i, (at, picture) in enumerate(shots):
        x, y = THUMB * (i % ACROSS), height * (i // ACROSS)
        sheet.paste(picture.convert("RGB").resize((THUMB, height)), (x, y))
        label = f"{int(at // 60)}:{int(at % 60):02d}"
        draw.rectangle((x, y, x + 8 * len(label) + 8, y + 16), fill="black")
        draw.text((x + 4, y + 2), label, fill="yellow")
    out = io.BytesIO()
    sheet.save(out, "JPEG", quality=70)
    return out.getvalue(), seconds, len(shots)


def speech(path: str) -> str:
    """What is said in the video, in its own language ("" when there is nothing to hear or no Whisper)."""
    try:
        from ..voice.speech import shared

        return shared("small", None).file(path)
    except ImportError:
        return ""
    except Exception as exc:  # a video with no sound track
        log.info("no speech from %s: %s", path, exc)
        return ""


def download(link: str, folder: Path) -> str:
    """A video from a link saved in ``folder`` (at most 720p), with yt-dlp; its path."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("За видео от линк трябва yt-dlp: `jarvis update` го инсталира. Или прикачи самия файл.") from exc
    folder.mkdir(parents=True, exist_ok=True)
    options = {"format": "best[height<=720][ext=mp4]/best[height<=720]/best", "quiet": True, "noprogress": True,
               "outtmpl": str(folder / "%(id)s.%(ext)s"), "max_filesize": 300_000_000}
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(link, download=True)
        return ydl.prepare_filename(info)


def register(registry: ToolRegistry, ctx) -> None:
    @registry.tool(
        "Watch a video: a video file on this computer, or a link (YouTube, TikTok, Instagram, Facebook...). You get "
        "one picture with 12 frames spread over the video (left to right, top to bottom, the time on each) and what "
        "is said in it, written out. Use it when the owner sends or names a video and wants to know what is in it.",
        obj({"source": ("string", "Path of the video file, or a link"),
             "question?": ("string", "What the owner wants to know from it")}),
    )
    def watch_video(source: str, question: str = ""):
        source = source.strip().strip('"')
        if "://" in source:
            source = download(source, Path(ctx.settings.home) / "uploads" / "videos")
        else:
            from .system import allowed_path

            source = str(allowed_path(source, ctx.settings.allowed_roots))
        try:
            sheet, seconds, count = frames(source)
        except ImportError as exc:
            raise RuntimeError("За гледане на видео трябват PyAV и Pillow (идват с `jarvis update`).") from exc
        said = speech(source) if seconds <= LONGEST_SPEECH else ""
        text = (f"{count} frames from {Path(source).name} ({int(seconds // 60)}:{int(seconds % 60):02d} long), "
                "left to right, top to bottom, the time on each.\n"
                + (f"What is said in it (Whisper): {said[:6000]}" if said else "No speech heard in it.")
                + (f"\nThe owner wants to know: {question}" if question else ""))
        return Image(sheet, "image/jpeg", text)

"""Watching a video on this computer: frames on one picture and the speech written out."""

import pytest

from jarvis.plugins import video


def make_video(path, seconds=4, fps=10):
    av = pytest.importorskip("av")
    np = pytest.importorskip("numpy")
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width, stream.height, stream.pix_fmt = 160, 96, "yuv420p"
        for i in range(seconds * fps):
            frame = av.VideoFrame.from_ndarray(np.full((96, 160, 3), i * 6 % 255, dtype=np.uint8), format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


def test_a_video_becomes_one_picture_of_its_frames(tmp_path):
    pytest.importorskip("PIL")
    sheet, seconds, count = video.frames(str(make_video(tmp_path / "clip.mp4")))
    assert sheet[:2] == b"\xff\xd8" and 3.5 < seconds < 4.5 and count == video.FRAMES


def test_watch_video_shows_the_frames_and_what_is_said(tmp_path, monkeypatch, settings, registry):
    pytest.importorskip("PIL")
    clip = make_video(tmp_path / "clip.mp4")
    monkeypatch.setattr(video, "speech", lambda path: "Здравей, това е Jarvis.")
    content, failed = registry.run("watch_video", {"source": str(clip), "question": "какво казва"}, lambda summary: True)
    assert not failed and content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/jpeg"
    assert "Здравей, това е Jarvis." in content[1]["text"] and "какво казва" in content[1]["text"]


def test_a_link_needs_yt_dlp(tmp_path, monkeypatch):
    import builtins

    real = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", lambda name, *a, **kw: (_ for _ in ()).throw(ImportError(name))
                        if name == "yt_dlp" else real(name, *a, **kw))
    with pytest.raises(RuntimeError, match="yt-dlp"):
        video.download("https://youtu.be/x", tmp_path)

from jarvis.voice.text import clean_for_speech, split_sentences


def test_clean_removes_markdown_code_and_links():
    text = "**Ето** списък:\n- [Google](https://google.com)\n- `pip install x`\n```py\nprint(1)\n```\nКрай: https://x.bg"
    assert clean_for_speech(text) == "Ето списък: Google pip install x Край:"


def test_split_sentences_keeps_short_and_cuts_long():
    assert split_sentences("Здравейте. Как сте? Добре!") == ["Здравейте.", "Как сте?", "Добре!"]
    long = "дума, " * 100
    chunks = split_sentences(long, max_chars=50)
    assert all(len(c) <= 51 for c in chunks)
    assert " ".join(chunks).replace(" ", "") == long.strip().replace(" ", "")

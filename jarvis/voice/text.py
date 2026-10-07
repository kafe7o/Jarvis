"""Подготовка на текст за изговаряне."""

from __future__ import annotations

import re

_CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_URL = re.compile(r"https?://\S+")
_MARKUP = re.compile(r"[*_#>~|]+")
_LIST_MARKER = re.compile(r"^\s*(?:[-+]|\d+[.)])\s+", re.MULTILINE)
_SPACES = re.compile(r"\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def clean_for_speech(text: str) -> str:
    """Маха Markdown, код и адреси, които звучат зле, когато се прочетат на глас."""
    text = _CODE_BLOCK.sub(" ", text)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub(" ", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _LIST_MARKER.sub("", text)
    text = _MARKUP.sub("", text)
    return _SPACES.sub(" ", text).strip()


def split_sentences(text: str, max_chars: int = 300) -> list[str]:
    """Разделя текста на изречения, за да започне говоренето по-бързо.

    Прекалено дълги изречения се режат по запетаи, а накрая и по думи.
    """
    chunks: list[str] = []
    for sentence in _SENTENCE_END.split(text.strip()):
        sentence = sentence.strip()
        while len(sentence) > max_chars:
            cut = sentence.rfind(",", 0, max_chars)
            if cut <= 0:
                cut = sentence.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            chunks.append(sentence[: cut + 1].strip())
            sentence = sentence[cut + 1 :].strip()
        if sentence:
            chunks.append(sentence)
    return chunks

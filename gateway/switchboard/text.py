"""Plain-text cleanup for TTS input. Models return markdown-formatted text
(headers, bold/italic, bullet lists, links) which a TTS voice reads out
literally ("pound pound pound one..."). Strips common markdown syntax
before synthesis -- not a full markdown parser, just the handful of
patterns actually observed in real responses."""

import re


def strip_markdown(text: str) -> str:
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)  # headers
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"\1", text)  # bold+italic
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)  # bold
    text = re.sub(r"\*(.+?)\*", r"\1", text)  # italic (asterisk)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", text)  # italic (underscore)
    text = re.sub(r"^\s*[-*]\s+", "", text, flags=re.MULTILINE)  # bullet markers
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)  # numbered list markers
    text = re.sub(r"^-{3,}\s*$", "", text, flags=re.MULTILINE)  # horizontal rules
    text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)  # links -> link text
    text = re.sub(r"`(.+?)`", r"\1", text)  # inline code
    return text

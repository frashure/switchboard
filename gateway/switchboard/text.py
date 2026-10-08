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


# Abbreviations whose trailing period does not end a sentence.
_ABBREVIATIONS = ("Mr", "Mrs", "Ms", "Dr", "Prof", "Sr", "Jr", "St", "vs", "Inc", "Ltd", "Co", "No", "e.g", "i.e")
_SENTENCE_BOUNDARY = re.compile(
    r"(?<=[.!?])"
    + "".join(rf"(?<!\b{re.escape(a)}\.)" for a in _ABBREVIATIONS)
    + r"(?<!\b[A-Z]\.)"  # initials: "J. R. R. Tolkien"
    + r"""["')\]]*\s+(?=["'(\[]?[A-Z0-9])"""
)
_CLAUSE_BOUNDARY = re.compile(r"(?<=[,;:])\s+")


def _split_long(piece: str, max_chars: int) -> list[str]:
    """Break an over-long sentence at clause punctuation, then at spaces,
    so one huge run-on can't delay the first audio like the whole answer
    used to."""
    if len(piece) <= max_chars:
        return [piece]
    parts: list[str] = []
    current = ""
    for clause in _CLAUSE_BOUNDARY.split(piece):
        if current and len(current) + 1 + len(clause) > max_chars:
            parts.append(current)
            current = clause
        else:
            current = f"{current} {clause}".strip()
    if current:
        parts.append(current)
    out: list[str] = []
    for part in parts:
        while len(part) > max_chars:
            cut = part.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            out.append(part[:cut].strip())
            part = part[cut:].strip()
        if part:
            out.append(part)
    return out


def split_for_speech(
    text: str,
    min_chars: int = 40,
    max_chars: int = 350,
    first_min_chars: int = 20,
    first_max_chars: int = 120,
) -> list[str]:
    """Split (already markdown-stripped) text into chunks to synthesize and
    play one after another, so audio can start after the first chunk rather
    than after the whole answer. Splits at paragraph and sentence boundaries
    (not inside abbreviations, initials or decimals), merges fragments
    shorter than min_chars into their neighbour (very short utterances
    sound choppy and add per-request overhead), and caps chunk length at
    max_chars."""
    sentences: list[str] = []
    for paragraph in re.split(r"\n+", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        for sentence in _SENTENCE_BOUNDARY.split(paragraph):
            # The first chunk is what the listener waits for, so it is kept
            # short; later chunks are synthesized while earlier ones play.
            limit = max_chars if sentences or len(sentence) <= first_max_chars else first_max_chars
            sentences.extend(_split_long(sentence.strip(), limit))

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if not sentence:
            continue
        current = f"{current} {sentence}".strip() if current else sentence
        if len(current) >= (min_chars if chunks else first_min_chars):
            chunks.append(current)
            current = ""
    if current:
        if chunks and len(current) < min_chars:
            chunks[-1] = f"{chunks[-1]} {current}"
        else:
            chunks.append(current)
    return chunks

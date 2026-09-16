from __future__ import annotations

from typing import Any, Iterator


_KNOWN_TRUNCATED_UTF8 = {
    "漫步è\u0080": "漫步者",
    "阿思ç¿": "阿思翠",
    "一å\u008a": "一加",
    "喜马拉é\u009b": "喜马拉雅",
    "进行æ\u0097线": "进行无线",
    "这款产å\u0093": "这款产品",
    "通话降å\u0099": "通话降噪",
}


def contains_c1_controls(value: str) -> bool:
    """Return whether text contains the controls produced by UTF-8 mojibake."""
    return any("\u0080" <= char <= "\u009f" for char in value)


def iter_display_strings(value: Any, path: str = "") -> Iterator[tuple[str, str]]:
    """Yield user-visible strings while excluding URL identity fields."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if key == "url":
                continue
            yield from iter_display_strings(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_display_strings(child, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value


def repair_utf8_mojibake(value: str) -> str:
    """Reverse UTF-8 text decoded through Latin-1/Windows-1252.

    This is a migration fallback.  New crawler responses must be decoded from
    their original bytes instead.  Invalid standalone bytes (for example a real
    non-breaking space mixed into otherwise broken text) are preserved.
    """
    for broken, corrected in _KNOWN_TRUNCATED_UTF8.items():
        value = value.replace(broken, corrected)

    reverse_single_byte: dict[str, int] = {chr(byte): byte for byte in range(256)}
    for byte in range(256):
        try:
            reverse_single_byte[bytes([byte]).decode("cp1252")] = byte
        except UnicodeDecodeError:
            continue

    raw = bytearray()
    for char in value:
        if char in reverse_single_byte:
            raw.append(reverse_single_byte[char])
        else:
            raw.extend(char.encode("utf-8"))
    decoded = raw.decode("utf-8", errors="surrogateescape")
    restored: list[str] = []
    for char in decoded:
        if not 0xDC80 <= ord(char) <= 0xDCFF:
            restored.append(char)
            continue
        byte = ord(char) - 0xDC00
        try:
            restored.append(bytes([byte]).decode("cp1252"))
        except UnicodeDecodeError:
            restored.append(chr(byte))
    repaired = "".join(restored)
    # A few legacy snapshots lost the final byte of a Chinese character before
    # they were committed.  These context-qualified terms cannot be recovered
    # from bytes alone, so keep the narrowly scoped historical corrections here.
    for broken, corrected in _KNOWN_TRUNCATED_UTF8.items():
        repaired = repaired.replace(broken, corrected)
    return repaired

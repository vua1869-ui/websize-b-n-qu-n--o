"""Tiện ích xử lý tiếng Việt: bỏ dấu để tìm kiếm không phân biệt dấu."""
import re
import unicodedata
from typing import Iterable, List


def normalize(text: str) -> str:
    """Chữ thường + bỏ dấu tiếng Việt: 'Áo Đi Biển' -> 'ao di bien'."""
    if not text:
        return ""
    text = text.replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[_\-/]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def tokens(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9]+", normalize(text)) if len(t) > 1]


def has_word(text_norm: str, phrase: str) -> bool:
    """Khớp nguyên từ/cụm từ (tránh 'm' khớp nhầm trong 'mình')."""
    phrase_norm = normalize(phrase)
    if not phrase_norm:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase_norm)}(?![a-z0-9])", text_norm) is not None


def has_any(text_norm: str, phrases: Iterable[str]) -> bool:
    return any(has_word(text_norm, p) for p in phrases)

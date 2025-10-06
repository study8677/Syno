from difflib import SequenceMatcher
from hashlib import sha1
from typing import Iterable


def normalize(text: str) -> str:
    return "".join(ch for ch in text.strip() if ch.isprintable())


def content_hash(text: str) -> str:
    return sha1(normalize(text).encode("utf-8")).hexdigest()


def is_similar(a: str, b: str, threshold: float = 0.92) -> bool:
    ratio = SequenceMatcher(None, normalize(a), normalize(b)).ratio()
    return ratio >= threshold


def is_duplicate(candidate: str, accepted: Iterable[str], threshold: float = 0.92) -> bool:
    for t in accepted:
        if is_similar(candidate, t, threshold=threshold):
            return True
    return False


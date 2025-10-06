import math


def quality_score(text: str) -> int:
    """Heuristic 0..100: structure + length + clarity proxies.
    Very lightweight to keep no heavy deps.
    """
    t = text.strip()
    if not t:
        return 0
    score = 0
    # length: ideal ~ 400-1000 chars
    n = len(t)
    if 300 <= n <= 1400:
        score += 40
    else:
        score += max(0, 40 - int(abs(n - 800) / 50))

    # basic structure hints
    bullets = sum(t.count(b) for b in ["\n- ", "\n* ", "\n1)", "\n1."])
    sections = sum(t.count(k) for k in ["结论", "依据", "方法", "步骤", "风险", "建议"])
    score += min(25, bullets * 5 + sections * 4)

    # readability proxy: punctuation and short lines
    lines = [l for l in t.splitlines() if l.strip()]
    if lines:
        avg_len = sum(len(l) for l in lines) / len(lines)
        if avg_len <= 48:
            score += 20
        else:
            score += max(0, 20 - int((avg_len - 48) / 4))

    # cap 0..100
    return max(0, min(100, score))


def hot_score(score: int, seconds_since_epoch: int) -> float:
    # simple Reddit-style-ish hot score: s / (t+2)^1.5
    return score / (pow(seconds_since_epoch + 2.0, 1.5))


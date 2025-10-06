from __future__ import annotations

import os
from typing import Optional

from sqlalchemy.orm import Session

from ..models import Answer, Consensus, Question


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _bool_env(name: str, default: bool) -> bool:
    return (_env(name, "1" if default else "0") in ("1", "true", "True", "yes", "on"))


def _int_env(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except Exception:
        return default


def ctx_from_dict(d: Optional[dict]):
    return {
        # 默认仅使用 Top-K，已移除共识生成
        "answer_ctx": (d or {}).get("answer_ctx") or _env("SYNO_ANSWER_CONTEXT", "topk"),
        "comment_ctx": (d or {}).get("comment_ctx") or _env("SYNO_COMMENT_CONTEXT", "topk"),
        "ctx_topk": int((d or {}).get("ctx_topk") or _int_env("SYNO_CONTEXT_TOPK", 2)),
        "ctx_snippet": int((d or {}).get("ctx_snippet") or _int_env("SYNO_CONTEXT_SNIPPET", 200)),
    }


def _snip(text: str, limit: int) -> str:
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    return t[: limit - 1] + "…"


def build_answer_background(db: Session, q: Question, override_cfg: Optional[dict]) -> str:
    cfg = ctx_from_dict(override_cfg)
    mode = (cfg.get("answer_ctx") or "none").lower()
    topk = int(cfg.get("ctx_topk", 2) or 2)
    sn = int(cfg.get("ctx_snippet", 200) or 200)
    parts: list[str] = []
    # consensus removed by default; keep no-op if chosen
    if mode in ("consensus", "both"):
        pass
    if mode in ("topk", "both"):
        ans = (
            db.query(Answer)
            .filter(Answer.question_id == q.id)
            .order_by(Answer.quality_score.desc())
            .limit(topk)
            .all()
        )
        if ans:
            bullets = [f"• {a.persona}：{_snip(a.content, sn)}" for a in ans]
            parts.append("参考要点：\n" + "\n".join(bullets))
    return "\n\n".join(parts).strip()


def build_comment_background(db: Session, q: Question, override_cfg: Optional[dict]) -> str:
    cfg = ctx_from_dict(override_cfg)
    mode = (cfg.get("comment_ctx") or "consensus").lower()
    topk = int(cfg.get("ctx_topk", 2) or 2)
    sn = int(cfg.get("ctx_snippet", 160) or 160)
    parts: list[str] = []
    if mode in ("consensus", "both"):
        pass
    if mode in ("topk", "both"):
        ans = (
            db.query(Answer)
            .filter(Answer.question_id == q.id)
            .order_by(Answer.quality_score.desc())
            .limit(topk)
            .all()
        )
        if ans:
            bullets = [f"• {a.persona}：{_snip(a.content, sn)}" for a in ans]
            parts.append("参考要点：\n" + "\n".join(bullets))
    return "\n\n".join(parts).strip()

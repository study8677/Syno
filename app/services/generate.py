import asyncio
import os
from typing import Optional

from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import Answer, Question, User, Persona, Comment, VoteTarget
from .dedupe import content_hash, is_duplicate
from .llm import LLMClient, config_from_dict
from .context import build_answer_background, build_comment_background


PERSONAS = ["学者", "工程师", "创作者"]


def _comment_personas_max() -> int:
    try:
        return max(1, int(os.getenv("SYNO_COMMENT_PERSONAS_MAX", "1")))
    except Exception:
        return 1


def _default_persona_prompt() -> str:
    return os.getenv(
        "SYNO_DEFAULT_PERSONA_PROMPT",
        (
            "友善、善良、积极正能量；尊重他人、简洁礼貌；"
            "偏好结构化中文短句，先结论后依据，强调可执行步骤与边界。"
        ),
    )


async def generate_for_question(
    question_id: int,
    user_preset: Optional[str] = None,
    override_cfg: Optional[dict] = None,
) -> None:
    db: Session = SessionLocal()
    try:
        q = db.get(Question, question_id)
        if not q:
            return

        cfg = config_from_dict(override_cfg) or None
        client = LLMClient(cfg)
        personas = list(PERSONAS)
        if user_preset:
            personas.append("我的人格")

        accepted_texts: list[str] = []
        answers_to_create: list[Answer] = []

        async def gen_one(persona: str):
            preset = user_preset if persona == "我的人格" else None
            background = build_answer_background(db, q, override_cfg)
            merged = q.content or ""
            if background:
                merged = (merged + "\n\n[背景]\n" + background).strip()
            txt = await client.generate_answer(
                persona=persona, title=q.title, content=(merged or None), user_preset=preset
            )
            score = await client.evaluate_quality(q.title, q.content or "", txt)
            return persona, txt, score

        gens = await asyncio.gather(*[gen_one(p) for p in personas])
        for persona, txt, score in gens:
            if not txt or is_duplicate(txt, accepted_texts):
                continue
            accepted_texts.append(txt)
            ans = Answer(
                question_id=q.id,
                persona=persona,
                content=txt,
                content_hash=content_hash(txt),
                quality_score=int(score),
            )
            answers_to_create.append(ans)

        for a in answers_to_create:
            db.add(a)
        db.commit()
    finally:
        db.close()


async def generate_user_personas_for_question(
    question_id: int,
    user_id: int,
    override_cfg: Optional[dict] = None,
    persona_ids: Optional[list[str]] = None,
) -> None:
    db: Session = SessionLocal()
    try:
        q = db.get(Question, question_id)
        user = db.get(User, user_id)
        if not q or not user:
            return
        personas = (
            db.query(Persona)
            .filter(Persona.user_id == user_id, Persona.is_active == 1)
            .order_by(Persona.id.asc())
            .all()
        )
        if persona_ids:
            selected: list[Persona] = []
            has_default = False
            for pid in persona_ids:
                if str(pid) == "default":
                    has_default = True
                else:
                    for p in personas:
                        if str(p.id) == str(pid):
                            selected.append(p)
                            break
            personas = selected
            if has_default:
                prompt = user.prompt_preset or _default_persona_prompt()
                personas.append(Persona(id=0, user_id=user_id, name="默认人格", prompt=prompt, is_active=1))  # type: ignore
        if not personas:
            prompt = user.prompt_preset or _default_persona_prompt()
            personas = [Persona(id=0, user_id=user_id, name="默认人格", prompt=prompt, is_active=1)]  # type: ignore

        cfg = config_from_dict(override_cfg) or None
        client = LLMClient(cfg)

        existing = [a.content for a in db.query(Answer).filter(Answer.question_id == q.id).all()]
        accepted = list(existing)
        created: list[Answer] = []

        async def gen(p: Persona):
            background = build_answer_background(db, q, override_cfg)
            merged = q.content or ""
            if background:
                merged = (merged + "\n\n[背景]\n" + background).strip()
            txt = await client.generate_answer(
                persona=p.name, title=q.title, content=(merged or None), user_preset=p.prompt
            )
            score = await client.evaluate_quality(q.title, q.content or "", txt)
            return p, txt, score

        gens = await asyncio.gather(*[gen(p) for p in personas])
        for p, txt, score in gens:
            if not txt or is_duplicate(txt, accepted):
                continue
            accepted.append(txt)
            a = Answer(
                question_id=q.id,
                persona=f"{p.name}（@{user.username}）",
                content=txt,
                content_hash=content_hash(txt),
                quality_score=int(score),
            )
            created.append(a)
        for a in created:
            db.add(a)
        db.commit()
    finally:
        db.close()


async def generate_comments_for_question(
    question_id: int,
    user_id: int,
    parent_id: Optional[int] = None,
    override_cfg: Optional[dict] = None,
    persona_id: Optional[str] = None,
) -> None:
    db: Session = SessionLocal()
    try:
        q = db.get(Question, question_id)
        user = db.get(User, user_id)
        if not q or not user:
            return
        personas = (
            db.query(Persona)
            .filter(Persona.user_id == user_id, Persona.is_active == 1)
            .order_by(Persona.id.asc())
            .all()
        )
        if persona_id:
            if persona_id == "default":
                prompt = user.prompt_preset or _default_persona_prompt()
                personas = [Persona(id=0, user_id=user_id, name="默认人格", prompt=prompt, is_active=1)]  # type: ignore
            else:
                personas = [p for p in personas if str(p.id) == str(persona_id)] or personas[:1]
        if not personas:
            prompt = user.prompt_preset or _default_persona_prompt()
            personas = [Persona(id=0, user_id=user_id, name="默认人格", prompt=prompt, is_active=1)]  # type: ignore
        personas = personas[: _comment_personas_max()]
        cfg = config_from_dict(override_cfg) or None
        client = LLMClient(cfg)
        parent_text = None
        if parent_id:
            pc = db.get(Comment, parent_id)
            parent_text = pc.content if pc else None

        async def gen(p: Persona):
            background = build_comment_background(db, q, override_cfg)
            merged = q.content or ""
            if background:
                merged = (merged + "\n\n[背景]\n" + background).strip()
            txt = await client.generate_comment(
                persona=p.name,
                title=q.title,
                content=(merged or None),
                reply_to=parent_text,
                user_preset=p.prompt,
            )
            return p, txt

        results = await asyncio.gather(*[gen(p) for p in personas])
        for p, txt in results:
            if not txt:
                continue
            c = Comment(
                user_id=user.id,
                target_type=VoteTarget.question,
                target_id=q.id,
                parent_id=parent_id,
                content=f"（{p.name}）{txt}",
            )
            db.add(c)
        db.commit()
    finally:
        db.close()


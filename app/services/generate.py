import asyncio
from typing import Optional

from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import Answer, Consensus, Question, User, Persona
from .dedupe import content_hash, is_duplicate
from .llm import LLMClient, LLMConfig, config_from_dict
from .ranking import quality_score


PERSONAS = ["学者", "工程师", "创作者"]


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

        # Generate answers concurrently
        async def gen_one(persona: str):
            preset = user_preset if persona == "我的人格" else None
            txt = await client.generate_answer(persona=persona, title=q.title, content=q.content, user_preset=preset)
            return persona, txt

        gens = await asyncio.gather(*[gen_one(p) for p in personas])
        for persona, txt in gens:
            if not txt or is_duplicate(txt, accepted_texts):
                continue
            accepted_texts.append(txt)
            ans = Answer(
                question_id=q.id,
                persona=persona,
                content=txt,
                content_hash=content_hash(txt),
                quality_score=quality_score(txt),
            )
            answers_to_create.append(ans)

        for a in answers_to_create:
            db.add(a)
        db.commit()

        # Build consensus
        if accepted_texts:
            cons = await client.summarize_consensus(q.title, accepted_texts)
            c = Consensus(
                question_id=q.id,
                conclusion=cons.get("conclusion", ""),
                evidence=cons.get("evidence", ""),
                divergence=cons.get("divergence", ""),
                summary=cons.get("summary", ""),
            )
            db.add(c)
            db.commit()
    finally:
        db.close()


async def generate_user_personas_for_question(
    question_id: int,
    user_id: int,
    override_cfg: Optional[dict] = None,
) -> None:
    db: Session = SessionLocal()
    try:
        q = db.get(Question, question_id)
        user = db.get(User, user_id)
        if not q or not user:
            return
        personas = db.query(Persona).filter(Persona.user_id == user_id, Persona.is_active == 1).order_by(Persona.id.asc()).all()
        if not personas:
            return
        cfg = config_from_dict(override_cfg) or None
        client = LLMClient(cfg)

        # existing texts for dedupe
        existing = [a.content for a in db.query(Answer).filter(Answer.question_id == q.id).all()]
        accepted = list(existing)
        created: list[Answer] = []

        async def gen(p: Persona):
            txt = await client.generate_answer(persona=p.name, title=q.title, content=q.content, user_preset=p.prompt)
            return p, txt

        gens = await asyncio.gather(*[gen(p) for p in personas])
        for p, txt in gens:
            if not txt or is_duplicate(txt, accepted):
                continue
            accepted.append(txt)
            a = Answer(
                question_id=q.id,
                persona=f"{p.name}（@{user.username}）",
                content=txt,
                content_hash=content_hash(txt),
                quality_score=quality_score(txt),
            )
            created.append(a)
        for a in created:
            db.add(a)
        db.commit()
    finally:
        db.close()

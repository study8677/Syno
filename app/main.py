import os
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from .db import init_db
from .auth import get_current_user, hash_password, verify_password, require_admin
from .db import get_session
from .models import User, Question, Answer, Vote, VoteTarget, Comment, Persona, PersonaHub
from sqlalchemy.orm import Session
from fastapi import BackgroundTasks
from .services.generate import (
    generate_for_question,
    generate_user_personas_for_question,
    generate_comments_for_question,
)


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


def create_app() -> FastAPI:
    app = FastAPI(title="Syno", version="0.1.0")

    secret_key = os.getenv("SYNO_SECRET_KEY", "dev-secret-change-me")
    app.add_middleware(SessionMiddleware, secret_key=secret_key)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @app.on_event("startup")
    async def _startup() -> None:
        # Initialize database tables
        init_db()
        # Purge legacy consensus data (feature removed)
        try:
            from .db import SessionLocal as _SL
            from .models import Consensus as _C
            s = _SL()
            try:
                s.query(_C).delete()
                s.commit()
            finally:
                s.close()
        except Exception:
            pass

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request, db: Session = Depends(get_session), user=Depends(get_current_user)):
        sort = request.query_params.get("sort", "hot")
        qs = db.query(Question).order_by(Question.created_at.desc()).limit(200).all()
        if sort == "hot":
            # compute a lightweight heat: question votes + answer votes + #answers
            q_scores = {
                q.id: sum(v for (v,) in db.query(Vote.value).filter(Vote.target_type == VoteTarget.question, Vote.target_id == q.id).all())
                for q in qs
            }
            ans = db.query(Answer).filter(Answer.question_id.in_([q.id for q in qs])).all()
            a_by_q: dict[int, list[Answer]] = {}
            for a in ans:
                a_by_q.setdefault(a.question_id, []).append(a)
            a_scores = {
                qid: sum(v for (v,) in db.query(Vote.value).filter(Vote.target_type == VoteTarget.answer, Vote.target_id.in_([a.id for a in alist])).all()) if alist else 0
                for qid, alist in a_by_q.items()
            }
            scores = []
            for q in qs:
                s = q_scores.get(q.id, 0) + a_scores.get(q.id, 0) + len(a_by_q.get(q.id, []))
                scores.append((s, q))
            scores.sort(key=lambda x: x[0], reverse=True)
            qs_sorted = [q for _, q in scores][:50]
        else:
            qs_sorted = qs[:50]
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "questions": qs_sorted, "user": user},
        )

    # --- Persona hub ---
    @app.get("/personas", response_class=HTMLResponse)
    async def personas_index(request: Request, db: Session = Depends(get_session), user=Depends(get_current_user)):
        sort = request.query_params.get("sort", "hot")
        qstr = (request.query_params.get("q") or "").strip()
        mine = request.query_params.get("mine") == "1"

        query = db.query(PersonaHub)
        if qstr:
            try:
                from sqlalchemy import or_
                query = query.filter(or_(PersonaHub.name.contains(qstr), PersonaHub.prompt.contains(qstr)))
            except Exception:
                pass
        if mine and user:
            query = query.filter(PersonaHub.source_user_id == user.id)

        items = query.order_by(PersonaHub.created_at.desc()).all()

        # compute likes/liked_by_me via votes
        likes_map = {
            it.id: sum(v for (v,) in db.query(Vote.value).filter(Vote.target_type == VoteTarget.persona, Vote.target_id == it.id).all())
            for it in items
        }
        liked_by_me = set(
            [hid for (hid,) in db.query(Vote.target_id).filter(user is not None, Vote.user_id == (user.id if user else 0), Vote.target_type == VoteTarget.persona, Vote.value == 1).all()]
        ) if user else set()

        data = [
            {
                "id": it.id,
                "name": it.name,
                "prompt": it.prompt,
                "owner": it.owner,
                "uses": it.uses_count,
                "likes": likes_map.get(it.id, 0),
                "liked": it.id in liked_by_me,
            }
            for it in items
        ]
        if sort == "hot":
            data.sort(key=lambda x: (x["likes"] * 2 + x["uses"]), reverse=True)
        return templates.TemplateResponse("personas_index.html", {"request": request, "items": data, "sort": sort, "q": qstr, "mine": mine, "user": user})

    @app.get("/personas/share", response_class=HTMLResponse)
    async def personas_share_get(request: Request, db: Session = Depends(get_session), user=Depends(get_current_user)):
        if not user:
            return RedirectResponse(url="/login", status_code=302)
        ps = db.query(Persona).filter(Persona.user_id == user.id).order_by(Persona.id.desc()).all()
        return templates.TemplateResponse("personas_share.html", {"request": request, "user": user, "personas": ps})

    @app.post("/personas/share")
    async def personas_share_post(request: Request, pid: int | None = Form(None), name: str | None = Form(None), prompt: str | None = Form(None), db: Session = Depends(get_session), user=Depends(get_current_user)):
        if not user:
            return RedirectResponse(url="/login", status_code=302)
        src = None
        if pid:
            src = db.query(Persona).filter(Persona.id == pid, Persona.user_id == user.id).one_or_none()
        pname = (name or (src.name if src else "鎴戠殑浜烘牸")).strip()[:50]
        pprompt = (prompt or (src.prompt if src else (user.prompt_preset or ""))).strip()
        if not pprompt:
            pprompt = "锛堢┖锛?
        hub = PersonaHub(source_user_id=user.id, name=pname, prompt=pprompt)
        db.add(hub)
        db.commit()
        return RedirectResponse(url="/personas", status_code=302)

    @app.post("/personas/{hid}/use")
    async def personas_use(request: Request, hid: int, db: Session = Depends(get_session), user=Depends(get_current_user)):
        if not user:
            return RedirectResponse(url="/login", status_code=302)
        item = db.get(PersonaHub, hid)
        if not item:
            return RedirectResponse(url="/personas", status_code=302)
        # copy to user's personas
        p = Persona(user_id=user.id, name=f"{item.name}", prompt=item.prompt, is_active=1)
        db.add(p)
        # bump uses
        item.uses_count = (item.uses_count or 0) + 1
        db.add(item)
        db.commit()
        return RedirectResponse(url="/me/personas", status_code=302)

    @app.post("/personas/{hid}/like")
    async def personas_like(request: Request, hid: int, db: Session = Depends(get_session), user=Depends(get_current_user)):
        if not user:
            return RedirectResponse(url="/login", status_code=302)
        v = (
            db.query(Vote)
            .filter(Vote.user_id == user.id, Vote.target_type == VoteTarget.persona, Vote.target_id == hid)
            .one_or_none()
        )
        if v:
            v.value = 0 if v.value == 1 else 1
        else:
            v = Vote(user_id=user.id, target_type=VoteTarget.persona, target_id=hid, value=1)
            db.add(v)
        db.commit()
        referer = request.headers.get("referer") or "/personas"
        return RedirectResponse(url=referer, status_code=302)

    @app.get("/ask", response_class=HTMLResponse)
    async def ask_get(request: Request, user=Depends(get_current_user)):
        cfg = request.session.get("llm_cfg") or {}
        return templates.TemplateResponse("ask.html", {"request": request, "user": user, "llm_cfg": cfg})

    @app.post("/ask")
    async def ask_post(
        request: Request,
        background_tasks: BackgroundTasks,
        title: str = Form(...),
        content: str | None = Form(None),
        db: Session = Depends(get_session),
        user=Depends(get_current_user),
    ):
        q = Question(title=title.strip(), content=(content or None), author_id=(user.id if user else None))
        db.add(q)
        db.commit()
        db.refresh(q)
        user_preset = getattr(user, "prompt_preset", None) if user else None
        override_cfg = request.session.get("llm_cfg")
        background_tasks.add_task(generate_for_question, q.id, user_preset, override_cfg)
        # also generate with user's active personas if logged in
        if user:
            background_tasks.add_task(generate_user_personas_for_question, q.id, int(user.id), override_cfg)
        return RedirectResponse(url=f"/q/{q.id}", status_code=302)

    @app.get("/login", response_class=HTMLResponse)
    async def login_get(request: Request):
        return templates.TemplateResponse("login.html", {"request": request})

    @app.post("/login")
    async def login_post(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        db: Session = Depends(get_session),
    ):
        user = db.query(User).filter(User.username == username).first()
        if not user or not verify_password(password, user.password_hash):
            return templates.TemplateResponse(
                "login.html",
                {"request": request, "error": "鐢ㄦ埛鍚嶆垨瀵嗙爜閿欒"},
                status_code=400,
            )
        request.session["user_id"] = int(user.id)
        return RedirectResponse(url="/", status_code=302)

    @app.get("/signup", response_class=HTMLResponse)
    async def signup_get(request: Request):
        return templates.TemplateResponse("signup.html", {"request": request})

    @app.post("/signup")
    async def signup_post(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        db: Session = Depends(get_session),
    ):
        exists = db.query(User).filter(User.username == username).first()
        if exists:
            return templates.TemplateResponse(
                "signup.html",
                {"request": request, "error": "鐢ㄦ埛鍚嶅凡瀛樺湪"},
                status_code=400,
            )
        user = User(username=username, password_hash=hash_password(password))
        db.add(user)
        db.commit()
        db.refresh(user)
        request.session["user_id"] = int(user.id)
        return RedirectResponse(url="/", status_code=302)

    @app.post("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse(url="/", status_code=302)

    # --- AI settings ---
    @app.get("/ai", response_class=HTMLResponse)
    async def ai_settings_get(request: Request, user=Depends(get_current_user)):
        cfg = request.session.get("llm_cfg") or {}
        # never echo api_key in the value attribute
        has_key = bool(cfg.get("api_key"))
        if has_key:
            cfg = {**cfg, "api_key": ""}
        # defaults for context cfg
        # Default both to 鍏辫瘑+Top-K锛岄伩鍏嶇敤鎴峰拷鐣ュ紑鍏?        cfg.setdefault("answer_ctx", cfg.get("answer_ctx", "both"))
        cfg.setdefault("comment_ctx", cfg.get("comment_ctx", "both"))
        cfg.setdefault("ctx_topk", cfg.get("ctx_topk", 2))
        cfg.setdefault("ctx_snippet", cfg.get("ctx_snippet", 200))
        return templates.TemplateResponse("ai_settings.html", {"request": request, "cfg": cfg, "has_key": has_key, "user": user})

    @app.post("/ai", response_class=HTMLResponse)
    async def ai_settings_post(
        request: Request,
        provider: str = Form("fake"),
        model: str = Form("gpt-4o-mini"),
        compat_name: str | None = Form(None),
        base_url: str | None = Form(None),
        api_key: str | None = Form(None),
        temperature: float = Form(0.4),
        answer_ctx: str = Form("none"),
        comment_ctx: str = Form("consensus"),
        ctx_topk: int = Form(2),
        ctx_snippet: int = Form(200),
        user=Depends(get_current_user),
    ):
        cfg = {
            "provider": provider,
            "model": model,
            "compat_name": compat_name or None,
            "base_url": base_url or None,
            "api_key": api_key or request.session.get("llm_cfg", {}).get("api_key"),  # keep existing if left blank
            "temperature": temperature,
            # context settings
            "answer_ctx": answer_ctx,
            "comment_ctx": comment_ctx,
            "ctx_topk": ctx_topk,
            "ctx_snippet": ctx_snippet,
        }
        request.session["llm_cfg"] = cfg
        return templates.TemplateResponse("ai_settings.html", {"request": request, "cfg": {**cfg, "api_key": ""}, "has_key": bool(cfg["api_key"]), "saved": True, "user": user})

    @app.post("/ai/test", response_class=HTMLResponse)
    async def ai_settings_test(request: Request, user=Depends(get_current_user)):
        # quick round-trip test
        override_cfg = request.session.get("llm_cfg")
        title = "Syno 杩炴帴鎬ф祴璇?
        content = "璇疯緭鍑轰竴娈典笉瓒呰繃30瀛楃殑涓枃鐭彞锛岃瘉鏄庢帴鍙ｅ彲鐢ㄣ€?
        from .services.llm import LLMClient, config_from_dict
        client = LLMClient(config_from_dict(override_cfg))
        try:
            text = await client.generate_answer("娴嬭瘯鍛?, title, content)
            ok = True
        except Exception as e:
            text = f"璋冪敤澶辫触锛歿e}"
            ok = False
        cfg = {**(override_cfg or {}), "api_key": ""}
        return templates.TemplateResponse("ai_settings.html", {"request": request, "cfg": cfg, "has_key": bool((override_cfg or {}).get("api_key")), "user": user, "test_result": text, "test_ok": ok})

    @app.get("/me/prompt", response_class=HTMLResponse)
    async def me_prompt_get(request: Request, user=Depends(get_current_user)):
        if not user:
            return RedirectResponse(url="/login", status_code=302)
        return templates.TemplateResponse("me_prompt.html", {"request": request, "user": user, "saved": False})

    @app.post("/me/prompt")
    async def me_prompt_post(request: Request, preset: str = Form(""), db: Session = Depends(get_session), user=Depends(get_current_user)):
        if not user:
            return RedirectResponse(url="/login", status_code=302)
        user.prompt_preset = (preset or None)
        db.add(user)
        db.commit()
        db.refresh(user)
        return templates.TemplateResponse("me_prompt.html", {"request": request, "user": user, "saved": True})

    @app.get("/q/{qid}", response_class=HTMLResponse)
    async def question_detail(request: Request, qid: int, db: Session = Depends(get_session), user=Depends(get_current_user)):
        q = db.get(Question, qid)
        if not q:
            return RedirectResponse(url="/", status_code=302)
        answers = db.query(Answer).filter(Answer.question_id == q.id).order_by(Answer.quality_score.desc()).all()
        # simple vote score aggregation
        def score_for(target_type: VoteTarget, target_id: int) -> int:
            return (
                db.query(Vote).filter(Vote.target_type == target_type, Vote.target_id == target_id).with_entities(Vote.value).all()
            )
        q_score = sum(v for (v,) in db.query(Vote.value).filter(Vote.target_type == VoteTarget.question, Vote.target_id == q.id).all())
        a_scores = {
            a.id: sum(v for (v,) in db.query(Vote.value).filter(Vote.target_type == VoteTarget.answer, Vote.target_id == a.id).all())
            for a in answers
        }
        # comments: load question's top-level and second-level
        comments = db.query(Comment).filter(Comment.target_type == VoteTarget.question, Comment.target_id == q.id).order_by(Comment.created_at.asc()).all()
        # group by parent
        top = [c for c in comments if c.parent_id is None]
        children = {}
        for c in comments:
            if c.parent_id:
                children.setdefault(c.parent_id, []).append(c)
        # my personas for selection UI
        my_personas = []
        if user:
            from .models import Persona
            my_personas = db.query(Persona).filter(Persona.user_id == user.id).order_by(Persona.id.desc()).all()

        # Can we generate consensus?
        return templates.TemplateResponse(
            "question_detail.html",
            {
                "request": request,
                "question": q,
                "answers": answers,
                "q_score": q_score,
                "a_scores": a_scores,
                "comments_top": top,
                "comments_children": children,
                "my_personas": my_personas,
                "user": user,
            },
        )

    @app.post("/q/{qid}/regen")
    async def question_regen(
        request: Request,
        qid: int,
        background_tasks: BackgroundTasks,
        db: Session = Depends(get_session),
        user=Depends(get_current_user),
    ):
        q = db.get(Question, qid)
        if not q:
            return RedirectResponse(url="/", status_code=302)
        # clear old answers/consensus
        db.query(Answer).filter(Answer.question_id == q.id).delete()
        db.query(Consensus).filter(Consensus.question_id == q.id).delete()
        db.commit()
        user_preset = getattr(user, "prompt_preset", None) if user else None
        override_cfg = request.session.get("llm_cfg")
        background_tasks.add_task(generate_for_question, q.id, user_preset, override_cfg)
        if user:
            background_tasks.add_task(generate_user_personas_for_question, q.id, int(user.id), override_cfg)
        return RedirectResponse(url=f"/q/{q.id}", status_code=302)

    @app.post("/q/{qid}/answer/mine")
    async def question_answer_mine(
        request: Request,
        qid: int,
        background_tasks: BackgroundTasks,
        db: Session = Depends(get_session),
        user=Depends(get_current_user),
        persona_ids: list[str] | None = Form(None),
    ):
        if not user:
            return RedirectResponse(url="/login", status_code=302)
        q = db.get(Question, qid)
        if not q:
            return RedirectResponse(url="/", status_code=302)
        override_cfg = request.session.get("llm_cfg")
        background_tasks.add_task(generate_user_personas_for_question, q.id, int(user.id), override_cfg, persona_ids)
        return RedirectResponse(url=f"/q/{q.id}", status_code=302)

    # Personas management
    @app.get("/me/personas", response_class=HTMLResponse)
    async def me_personas_get(request: Request, db: Session = Depends(get_session), user=Depends(get_current_user)):
        if not user:
            return RedirectResponse(url="/login", status_code=302)
        from .models import Persona
        ps = db.query(Persona).filter(Persona.user_id == user.id).order_by(Persona.id.desc()).all()
        return templates.TemplateResponse("me_personas.html", {"request": request, "user": user, "personas": ps})

    @app.post("/me/personas")
    async def me_personas_post(request: Request, name: str = Form(...), prompt: str = Form(""), db: Session = Depends(get_session), user=Depends(get_current_user)):
        if not user:
            return RedirectResponse(url="/login", status_code=302)
        from .models import Persona
        p = Persona(user_id=user.id, name=name.strip()[:50], prompt=(prompt or "").strip())
        db.add(p)
        db.commit()
        return RedirectResponse(url="/me/personas", status_code=302)

    # consensus removed

    @app.post("/me/personas/{pid}/delete")
    async def me_personas_delete(request: Request, pid: int, db: Session = Depends(get_session), user=Depends(get_current_user)):
        if not user:
            return RedirectResponse(url="/login", status_code=302)
        from .models import Persona
        db.query(Persona).filter(Persona.id == pid, Persona.user_id == user.id).delete()
        db.commit()
        return RedirectResponse(url="/me/personas", status_code=302)

    @app.post("/me/personas/{pid}/toggle")
    async def me_personas_toggle(request: Request, pid: int, db: Session = Depends(get_session), user=Depends(get_current_user)):
        if not user:
            return RedirectResponse(url="/login", status_code=302)
        from .models import Persona
        p = db.query(Persona).filter(Persona.id == pid, Persona.user_id == user.id).one_or_none()
        if p:
            p.is_active = 0 if p.is_active else 1
            db.add(p)
            db.commit()
        return RedirectResponse(url="/me/personas", status_code=302)

    @app.post("/vote")
    async def vote(
        request: Request,
        target_type: str = Form(...),
        target_id: int = Form(...),
        value: int = Form(...),
        db: Session = Depends(get_session),
        user=Depends(get_current_user),
    ):
        if not user:
            return RedirectResponse(url="/login", status_code=302)
        try:
            ttype = VoteTarget(target_type)
        except Exception:
            return RedirectResponse(url="/", status_code=302)
        # upsert logic
        v = (
            db.query(Vote)
            .filter(
                Vote.user_id == user.id, Vote.target_type == ttype, Vote.target_id == int(target_id)
            )
            .one_or_none()
        )
        if v:
            # Toggle behavior: clicking the same choice again clears the vote
            iv = int(value)
            v.value = 0 if v.value == iv else iv
        else:
            v = Vote(user_id=user.id, target_type=ttype, target_id=int(target_id), value=int(value))
            db.add(v)
        db.commit()
        # redirect back
        referer = request.headers.get("referer") or "/"
        return RedirectResponse(url=referer, status_code=302)

    @app.post("/comment")
    async def comment(
        request: Request,
        target_type: str = Form(...),
        target_id: int = Form(...),
        content: str = Form(...),
        parent_id: int | None = Form(None),
        db: Session = Depends(get_session),
        user=Depends(get_current_user),
    ):
        # 绂佹鎵嬪伐璇勮锛氱粺涓€閫氳繃 AI 鐢熸垚
        referer = request.headers.get("referer") or "/"
        return RedirectResponse(url=referer, status_code=302)

    @app.post("/q/{qid}/comment/ai")
    async def comment_ai(
        request: Request,
        qid: int,
        background_tasks: BackgroundTasks,
        user=Depends(get_current_user),
        persona_id: str | None = Form(None),
    ):
        if not user:
            return RedirectResponse(url="/login", status_code=302)
        override_cfg = request.session.get("llm_cfg")
        # 鐩存帴绛夊緟鐢熸垚锛岀偣鍑诲悗鍗冲彲鐪嬪埌缁撴灉
        await generate_comments_for_question(qid, int(user.id), None, override_cfg, persona_id)
        return RedirectResponse(url=f"/q/{qid}", status_code=302)

    @app.post("/comment/{cid}/reply/ai")
    async def comment_reply_ai(
        request: Request,
        cid: int,
        background_tasks: BackgroundTasks,
        db: Session = Depends(get_session),
        user=Depends(get_current_user),
        persona_id: str | None = Form(None),
    ):
        if not user:
            return RedirectResponse(url="/login", status_code=302)
        c = db.get(Comment, cid)
        if not c:
            return RedirectResponse(url="/", status_code=302)
        override_cfg = request.session.get("llm_cfg")
        await generate_comments_for_question(int(c.target_id), int(user.id), int(cid), override_cfg, persona_id)
        return RedirectResponse(url=f"/q/{c.target_id}", status_code=302)

    # --- Admin (requires SYNO_ADMIN_USERS contain username) ---
    @app.get("/admin", response_class=HTMLResponse)
    async def admin_index(request: Request, tab: str = "questions", q: str | None = None, db: Session = Depends(get_session), user=Depends(require_admin)):
        headers = []
        rows = []
        query_str = (q or "").strip()
        if tab == "users":
            items = db.query(User).order_by(User.id.desc()).all()
            if query_str:
                items = [u for u in items if query_str in u.username]
            headers = ["ID", "用户名", "创建时间"]
            for u in items[:200]:
                rows.append({"cells": [u.id, u.username, getattr(u, 'created_at', '')], "delete_action": None})
        elif tab == "answers":
            items = db.query(Answer).order_by(Answer.id.desc()).limit(200).all()
            if query_str:
                items = [a for a in items if query_str in (a.content or '')]
            headers = ["ID", "QID", "人格", "质量", "内容"]
            for a in items:
                body = (a.content or '')
                short = body[:120] + ('…' if len(body) > 120 else '')
                rows.append({"cells": [a.id, a.question_id, a.persona, a.quality_score, short], "delete_action": f"/admin/delete/answer/{a.id}"})
        elif tab == "comments":
            items = db.query(Comment).order_by(Comment.id.desc()).limit(200).all()
            if query_str:
                items = [c for c in items if query_str in (c.content or '')]
            headers = ["ID", "目标", "父ID", "内容"]
            for c in items:
                body = (c.content or '')
                short = body[:120] + ('…' if len(body) > 120 else '')
                rows.append({"cells": [c.id, f"{c.target_type}:{c.target_id}", c.parent_id or '-', short], "delete_action": f"/admin/delete/comment/{c.id}"})
        elif tab == "personas":
            items = db.query(Persona).order_by(Persona.id.desc()).limit(200).all()
            if query_str:
                items = [p for p in items if query_str in p.name or query_str in (p.prompt or '')]
            headers = ["ID", "用户", "名称", "状态", "提示词"]
            for p in items:
                body = (p.prompt or '')
                short = body[:120] + ('…' if len(body) > 120 else '')
                rows.append({"cells": [p.id, p.user_id, p.name, ("启用" if getattr(p, 'is_active', 1) else "停用"), short], "delete_action": f"/admin/delete/persona/{p.id}"})
        elif tab == "hub":
            items = db.query(PersonaHub).order_by(PersonaHub.id.desc()).limit(200).all()
            if query_str:
                items = [h for h in items if query_str in h.name or query_str in (h.prompt or '')]
            headers = ["ID", "作者", "名称", "使用", "提示词"]
            for h in items:
                body = (h.prompt or '')
                short = body[:120] + ('…' if len(body) > 120 else '')
                rows.append({"cells": [h.id, h.source_user_id, h.name, h.uses_count, short], "delete_action": f"/admin/delete/hub/{h.id}"})
        else:
            items = db.query(Question).order_by(Question.id.desc()).limit(200).all()
            if query_str:
                items = [qq for qq in items if query_str in (qq.title or '') or query_str in (qq.content or '')]
            headers = ["ID", "标题", "内容", "创建时间"]
            for qq in items:
                body = (qq.content or '')
                short = body[:120] + ('…' if len(body) > 120 else '')
                rows.append({"cells": [qq.id, qq.title, short, getattr(qq, 'created_at', '')], "delete_action": f"/admin/delete/question/{qq.id}"})

        return templates.TemplateResponse("admin_index.html", {"request": request, "user": user, "tab": tab, "q": query_str, "headers": headers, "rows": rows})

    @app.post("/admin/delete/question/{qid}")
    async def admin_delete_question(request: Request, qid: int, db: Session = Depends(get_session), user=Depends(require_admin)):
        db.query(Answer).filter(Answer.question_id == qid).delete()
        db.query(Comment).filter(Comment.target_type == VoteTarget.question, Comment.target_id == qid).delete()
        db.query(Question).filter(Question.id == qid).delete()
        db.commit()
        return RedirectResponse(url="/admin?tab=questions", status_code=302)

    @app.post("/admin/delete/answer/{aid}")
    async def admin_delete_answer(request: Request, aid: int, db: Session = Depends(get_session), user=Depends(require_admin)):
        db.query(Answer).filter(Answer.id == aid).delete()
        db.commit()
        return RedirectResponse(url="/admin?tab=answers", status_code=302)

    @app.post("/admin/delete/comment/{cid}")
    async def admin_delete_comment(request: Request, cid: int, db: Session = Depends(get_session), user=Depends(require_admin)):
        db.query(Comment).filter(Comment.id == cid).delete()
        db.commit()
        return RedirectResponse(url="/admin?tab=comments", status_code=302)

    @app.post("/admin/delete/persona/{pid}")
    async def admin_delete_persona(request: Request, pid: int, db: Session = Depends(get_session), user=Depends(require_admin)):
        db.query(Persona).filter(Persona.id == pid).delete()
        db.commit()
        return RedirectResponse(url="/admin?tab=personas", status_code=302)

    @app.post("/admin/delete/hub/{hid}")
    async def admin_delete_hub(request: Request, hid: int, db: Session = Depends(get_session), user=Depends(require_admin)):
        db.query(PersonaHub).filter(PersonaHub.id == hid).delete()
        db.commit()
        return RedirectResponse(url="/admin?tab=hub", status_code=302)

    return app


app = create_app()


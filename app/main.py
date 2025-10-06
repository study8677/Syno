import os
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from .db import init_db
from .auth import get_current_user, hash_password, verify_password
from .db import get_session
from .models import User, Question, Answer, Consensus, Vote, VoteTarget, Comment
from sqlalchemy.orm import Session
from fastapi import BackgroundTasks
from .services.generate import generate_for_question, generate_user_personas_for_question


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
                {"request": request, "error": "用户名或密码错误"},
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
                {"request": request, "error": "用户名已存在"},
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
        user=Depends(get_current_user),
    ):
        cfg = {
            "provider": provider,
            "model": model,
            "compat_name": compat_name or None,
            "base_url": base_url or None,
            "api_key": api_key or request.session.get("llm_cfg", {}).get("api_key"),  # keep existing if left blank
            "temperature": temperature,
        }
        request.session["llm_cfg"] = cfg
        return templates.TemplateResponse("ai_settings.html", {"request": request, "cfg": {**cfg, "api_key": ""}, "has_key": bool(cfg["api_key"]), "saved": True, "user": user})

    @app.post("/ai/test", response_class=HTMLResponse)
    async def ai_settings_test(request: Request, user=Depends(get_current_user)):
        # quick round-trip test
        override_cfg = request.session.get("llm_cfg")
        title = "Syno 连接性测试"
        content = "请输出一段不超过30字的中文短句，证明接口可用。"
        from .services.llm import LLMClient, config_from_dict
        client = LLMClient(config_from_dict(override_cfg))
        try:
            text = await client.generate_answer("测试员", title, content)
            ok = True
        except Exception as e:
            text = f"调用失败：{e}"
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
        consensus = db.query(Consensus).filter(Consensus.question_id == q.id).one_or_none()
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

        return templates.TemplateResponse(
            "question_detail.html",
            {
                "request": request,
                "question": q,
                "consensus": consensus,
                "answers": answers,
                "q_score": q_score,
                "a_scores": a_scores,
                "comments_top": top,
                "comments_children": children,
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
    ):
        if not user:
            return RedirectResponse(url="/login", status_code=302)
        q = db.get(Question, qid)
        if not q:
            return RedirectResponse(url="/", status_code=302)
        override_cfg = request.session.get("llm_cfg")
        background_tasks.add_task(generate_user_personas_for_question, q.id, int(user.id), override_cfg)
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
            v.value = int(value)
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
        if not user:
            return RedirectResponse(url="/login", status_code=302)
        try:
            ttype = VoteTarget(target_type)
        except Exception:
            return RedirectResponse(url="/", status_code=302)
        text = (content or "").strip()
        if not text:
            referer = request.headers.get("referer") or "/"
            return RedirectResponse(url=referer, status_code=302)
        c = Comment(
            user_id=user.id,
            target_type=ttype,
            target_id=int(target_id),
            parent_id=int(parent_id) if parent_id else None,
            content=text[:4000],
        )
        db.add(c)
        db.commit()
        referer = request.headers.get("referer") or "/"
        return RedirectResponse(url=referer, status_code=302)

    return app


app = create_app()

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class VoteTarget(str, Enum):
    question = "question"
    answer = "answer"
    persona = "persona"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    prompt_preset: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    questions: Mapped[list[Question]] = relationship("Question", back_populates="author")  # type: ignore[name-defined]
    comments: Mapped[list[Comment]] = relationship("Comment", back_populates="author")  # type: ignore[name-defined]
    personas: Mapped[list["Persona"]] = relationship("Persona", back_populates="owner", cascade="all, delete-orphan")


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), index=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    author_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    author: Mapped[Optional[User]] = relationship("User", back_populates="questions")
    answers: Mapped[list[Answer]] = relationship("Answer", back_populates="question", cascade="all, delete-orphan")  # type: ignore[name-defined]
    consensus: Mapped[Optional[Consensus]] = relationship("Consensus", back_populates="question", uselist=False)  # type: ignore[name-defined]


class Answer(Base):
    __tablename__ = "answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(Integer, ForeignKey("questions.id"), index=True)
    persona: Mapped[str] = mapped_column(String(50), index=True)
    content: Mapped[str] = mapped_column(Text)
    quality_score: Mapped[float] = mapped_column(
        # store as int0..100 for simplicity; adjust later
        Integer, default=0
    )
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    question: Mapped[Question] = relationship("Question", back_populates="answers")  # type: ignore[name-defined]


class Consensus(Base):
    __tablename__ = "consensus"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(Integer, ForeignKey("questions.id"), unique=True)
    conclusion: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(Text)
    divergence: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    question: Mapped[Question] = relationship("Question", back_populates="consensus")  # type: ignore[name-defined]


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (
        UniqueConstraint("user_id", "target_type", "target_id", name="uq_vote_once"),
        CheckConstraint("value in (-1, 0, 1)", name="ck_vote_value"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    target_type: Mapped[VoteTarget] = mapped_column(SAEnum(VoteTarget))
    target_id: Mapped[int] = mapped_column(Integer)
    value: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    target_type: Mapped[VoteTarget] = mapped_column(SAEnum(VoteTarget))
    target_id: Mapped[int] = mapped_column(Integer)
    parent_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("comments.id"), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    author: Mapped[User] = relationship("User", back_populates="comments")  # type: ignore[name-defined]
    parent: Mapped[Optional[Comment]] = relationship("Comment", remote_side="Comment.id")  # type: ignore[name-defined]


class Persona(Base):
    __tablename__ = "personas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(50))
    prompt: Mapped[str] = mapped_column(Text)
    is_active: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    owner: Mapped[User] = relationship("User", back_populates="personas")


class PersonaHub(Base):
    __tablename__ = "persona_hub"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(50))
    prompt: Mapped[str] = mapped_column(Text)
    uses_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    owner: Mapped[User] = relationship("User")

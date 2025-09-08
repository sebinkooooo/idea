# backend/models.py
from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from db import Base  # 🔑 import Base from db.py

class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Idea(Base):
    __tablename__ = "ideas"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    public_md = Column(Text, nullable=True)
    private_md = Column(Text, nullable=True)
    visibility = Column(String, default="public")  # public / private / password
    parent_id = Column(String, ForeignKey("ideas.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    clonable = Column(Boolean, default=True)
    password_hash = Column(String, nullable=True)
    share_hash = Column(String, default=lambda: str(uuid.uuid4()), unique=True)

    owner = relationship("User", backref="ideas")


class Asset(Base):
    __tablename__ = "assets"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    idea_id = Column(String, ForeignKey("ideas.id"), nullable=False)
    type = Column(String, nullable=False)  # e.g., "image", "pdf", "link"
    title = Column(String, nullable=False)
    url = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    visibility = Column(String, default="public")

    idea = relationship("Idea", backref="assets")


class RepoItem(Base):
    __tablename__ = "repo_items"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    idea_id = Column(String, ForeignKey("ideas.id"), nullable=False)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)  # "link", "file", "note", "qa"
    url = Column(String, nullable=True)
    content = Column(Text, nullable=True)
    visibility = Column(String, default="public")
    created_at = Column(DateTime, default=datetime.utcnow)

    idea = relationship("Idea", backref="repo")

class QAHistory(Base):
    __tablename__ = "qa_history"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    idea_id = Column(String, ForeignKey("ideas.id"), nullable=False)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    idea = relationship("Idea", backref="qa_history")


class UnansweredQuestion(Base):
    __tablename__ = "unanswered_questions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    idea_id = Column(String, ForeignKey("ideas.id"), nullable=False)
    question = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    idea = relationship("Idea", backref="unanswered_questions")

class FormChatRequest(BaseModel):
    message: str

class FormChatResponse(BaseModel):
    reply: str
    updated_public_md: Optional[str] = None
    updated_private_md: Optional[str] = None

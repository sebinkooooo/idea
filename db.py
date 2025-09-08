# backend/db.py
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Use Postgres if DATABASE_URL is set, else fallback to SQLite
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./ideas.db")

# Only SQLite needs check_same_thread
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

# 🔑 Shared Base for all models
Base = declarative_base()

# FastAPI dependency
def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Called once in main.py to create tables
def init_db():
    import backend.models  # ensure models are imported
    Base.metadata.create_all(bind=engine)
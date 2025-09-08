from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List

import db, models
from ideas.main import IdeaResponse

router = APIRouter()

@router.get("/", response_model=List[IdeaResponse])
def get_feed(session: Session = Depends(db.get_session)):
    """List all public ideas (latest first)"""
    ideas = (
        session.query(models.Idea)
        .filter(models.Idea.visibility == "public")
        .order_by(models.Idea.created_at.desc())
        .all()
    )
    return [IdeaResponse.from_orm(i) for i in ideas]
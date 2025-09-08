from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List

from backend import db, models
from backend.auth.main import get_current_user
from backend.ideas.main import IdeaResponse

router = APIRouter()

@router.get("/", response_model=dict)
def get_home(
    session: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user)
):
    """Return the current user's dashboard: profile + ideas"""
    ideas = session.query(models.Idea).filter(models.Idea.user_id == current_user.id).all()
    return {
        "user": {
            "id": current_user.id,
            "name": current_user.name,
            "email": current_user.email,
        },
        "ideas": [IdeaResponse.from_orm(i) for i in ideas],
    }
# feed/main.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List

import db, models
from ideas.main import IdeaResponse
from auth.main import get_current_user  # ⬅️ require auth for /mine

router = APIRouter()

@router.get("/", response_model=List[IdeaResponse])
def get_feed(session: Session = Depends(db.get_session)):
    """List all public ideas (latest first)."""
    ideas = (
        session.query(models.Idea)
        .filter(models.Idea.visibility == "public")
        .order_by(models.Idea.created_at.desc())
        .all()
    )

    results: List[IdeaResponse] = []
    for idea in ideas:
        owner = session.query(models.User).get(idea.user_id)
        owner_name = owner.name if owner else None
        results.append(
            IdeaResponse.model_validate(idea, from_attributes=True).model_copy(
                update={"owner_name": owner_name}
            )
        )
    return results


@router.get("/mine", response_model=List[IdeaResponse])
def get_my_feed(
    session: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user),  # ⬅️ only owner’s ideas
):
    """List ONLY the current user's ideas (latest first)."""
    ideas = (
        session.query(models.Idea)
        .filter(models.Idea.user_id == current_user.id)
        .order_by(models.Idea.created_at.desc())
        .all()
    )

    # owner is the current user, but we still populate owner_name for consistency
    results: List[IdeaResponse] = []
    for idea in ideas:
        results.append(
            IdeaResponse.model_validate(idea, from_attributes=True).model_copy(
                update={"owner_name": current_user.name}
            )
        )
    return results
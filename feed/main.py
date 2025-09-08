# feed/main.py
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

    results: List[IdeaResponse] = []
    for idea in ideas:
        owner = session.query(models.User).get(idea.user_id)
        owner_name = owner.name if owner else None

        # ✅ Use Pydantic v2 model_validate
        results.append(
            IdeaResponse.model_validate(idea, from_attributes=True).model_copy(
                update={"owner_name": owner_name}
            )
        )

    return results
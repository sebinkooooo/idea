from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional

import db, models
from ideas.main import IdeaResponse
from ideas.main import check_password  # reuse helper

router = APIRouter()

@router.get("/{share_hash}", response_model=IdeaResponse)
def get_shared_idea(
    share_hash: str,
    password: Optional[str] = Query(None),
    session: Session = Depends(db.get_session)
):
    """Access an idea via its shareable link"""
    idea = session.query(models.Idea).filter(models.Idea.share_hash == share_hash).first()
    if not idea:
        raise HTTPException(status_code=404, detail="Shared idea not found")

    # Visibility rules
    if idea.visibility == "private":
        raise HTTPException(status_code=403, detail="This idea is private and cannot be shared")

    if idea.visibility == "password":
        if not password or not check_password(password, idea.password_hash):
            raise HTTPException(status_code=403, detail="Password required or incorrect")

    # Attach public owner name so clients don't show "Anonymous"
    owner = session.query(models.User).get(idea.user_id)
    owner_name = owner.name if owner else None

    return IdeaResponse.model_validate(idea, from_attributes=True).model_copy(
        update={"owner_name": owner_name}
    )
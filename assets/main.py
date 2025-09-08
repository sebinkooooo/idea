from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional

import db, models
from auth.main import get_current_user

router = APIRouter()

# ==== Schemas ====
class AssetCreate(BaseModel):
    type: str        # "image", "pdf", "link"
    title: str
    url: str
    description: Optional[str] = None
    visibility: str = "public"

class AssetResponse(AssetCreate):
    id: str
    idea_id: str

    class Config:
        orm_mode = True


# ==== Routes ====

@router.post("/ideas/{idea_id}/assets", response_model=AssetResponse)
def add_asset(
    idea_id: str,
    req: AssetCreate,
    session: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user)
):
    """Attach an asset to an idea"""
    idea = session.query(models.Idea).get(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    if idea.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your idea")

    asset = models.Asset(
        idea_id=idea.id,
        type=req.type,
        title=req.title,
        url=req.url,
        description=req.description,
        visibility=req.visibility,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset


@router.get("/ideas/{idea_id}/assets", response_model=List[AssetResponse])
def list_assets(idea_id: str, session: Session = Depends(db.get_session)):
    """List all public assets for an idea"""
    idea = session.query(models.Idea).get(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    return [a for a in idea.assets if a.visibility == "public"]
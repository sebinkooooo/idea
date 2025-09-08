from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from typing import List

from backend import db, models
from backend.auth.main import get_current_user

router = APIRouter()

# ==== Schemas ====
class UserProfile(BaseModel):
    id: str
    name: str
    email: EmailStr

    class Config:
        orm_mode = True

class UpdateUserRequest(BaseModel):
    name: str | None = None
    email: EmailStr | None = None


# ==== Routes ====

@router.get("/me", response_model=UserProfile)
def get_me(current_user: models.User = Depends(get_current_user)):
    """Get current user's profile"""
    return current_user


@router.patch("/me", response_model=UserProfile)
def update_me(
    req: UpdateUserRequest,
    session: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user)
):
    """Update current user's profile"""
    if req.name:
        current_user.name = req.name
    if req.email:
        # prevent duplicate email
        exists = session.query(models.User).filter(models.User.email == req.email).first()
        if exists and exists.id != current_user.id:
            raise HTTPException(status_code=400, detail="Email already in use")
        current_user.email = req.email

    session.add(current_user)
    session.commit()
    session.refresh(current_user)
    return current_user


@router.delete("/me")
def delete_me(
    session: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user)
):
    """Delete current user's account"""
    session.delete(current_user)
    session.commit()
    return {"detail": "User deleted"}


@router.get("/me/ideas")
def list_my_ideas(
    session: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user)
):
    """List all ideas owned by the current user"""
    ideas = session.query(models.Idea).filter(models.Idea.user_id == current_user.id).all()
    return ideas
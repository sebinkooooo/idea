from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import uuid, hashlib

import db, models
from auth.main import get_current_user
from chat.openai_helper import ask_openai

router = APIRouter()

# ==== Schemas ====
class IdeaSubmission(BaseModel):
    title: str
    notes: Optional[str] = None
    links: Optional[List[str]] = []
    summary: Optional[str] = None
    visibility: str = "public"  # public, private, password
    password: Optional[str] = None
    clonable: bool = True

class UpdateIdeaRequest(BaseModel):
    title: Optional[str] = None
    public_md: Optional[str] = None
    private_md: Optional[str] = None
    visibility: Optional[str] = None
    password: Optional[str] = None
    clonable: Optional[bool] = None

class IdeaResponse(BaseModel):
    id: str
    user_id: str
    title: str
    public_md: Optional[str]
    private_md: Optional[str]
    visibility: str
    parent_id: Optional[str] = None
    clonable: bool
    share_hash: Optional[str] = None  # ✅ add this

    class Config:
        from_attributes = True


# ==== Helpers ====
def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def check_password(pw: str, hash_val: str) -> bool:
    return hash_password(pw) == hash_val

def generate_markdown_from_submission(title: str, notes: str, links: List[str], summary: str):
    context = f"""
# TITLE
{title}

# SUMMARY
{summary or ""}

# NOTES
{notes or ""}

# LINKS
{", ".join(links or [])}
"""

    public_prompt = f"Turn this into a clear, inspiring public-facing markdown page:\n{context}"
    private_prompt = f"Turn this into exhaustive private notes for the creator:\n{context}"

    public_md = ask_openai(public_prompt, "Generate public markdown page")
    private_md = ask_openai(private_prompt, "Generate private markdown page")

    return public_md, private_md, context


# ==== Routes ====

@router.post("/", response_model=IdeaResponse)
def create_idea(
    req: IdeaSubmission,
    session: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user)
):
    """Create a new idea from raw submission (AI generates markdown)"""
    public_md, private_md, raw_context = generate_markdown_from_submission(
        req.title, req.notes, req.links, req.summary
    )

    password_hash = None
    if req.visibility == "password":
        if not req.password:
            raise HTTPException(status_code=400, detail="Password required for password-protected ideas")
        password_hash = hash_password(req.password)

    idea = models.Idea(
        user_id=current_user.id,
        title=req.title,
        public_md=public_md,
        private_md=private_md,
        visibility=req.visibility,
        password_hash=password_hash,
        clonable=req.clonable,
    )
    session.add(idea)
    session.commit()
    session.refresh(idea)

    # Save raw submission in repo
    raw_repo_item = models.RepoItem(
        idea_id=idea.id,
        name="Raw Submission",
        type="raw_submission",
        content=raw_context,
        visibility="private",
        created_at=datetime.utcnow()
    )
    session.add(raw_repo_item)
    session.commit()

    return idea


@router.get("/{idea_id}", response_model=IdeaResponse)
def get_idea(
    idea_id: str,
    password: Optional[str] = Query(None),
    session: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user)
):
    idea = session.query(models.Idea).get(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")

    # Respect privacy
    if idea.visibility == "private" and idea.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="This idea is private")

    if idea.visibility == "password" and idea.user_id != current_user.id:
        if not password or not check_password(password, idea.password_hash):
            raise HTTPException(status_code=403, detail="Password required or incorrect")

    return idea


@router.patch("/{idea_id}", response_model=IdeaResponse)
def update_idea(
    idea_id: str,
    req: UpdateIdeaRequest,
    session: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user)
):
    idea = session.query(models.Idea).get(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    if idea.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your idea")

    if req.title is not None: idea.title = req.title
    if req.public_md is not None: idea.public_md = req.public_md
    if req.private_md is not None: idea.private_md = req.private_md
    if req.visibility is not None: idea.visibility = req.visibility
    if req.password is not None: idea.password_hash = hash_password(req.password)
    if req.clonable is not None: idea.clonable = req.clonable

    session.commit()
    session.refresh(idea)
    return idea


@router.delete("/{idea_id}")
def delete_idea(
    idea_id: str,
    session: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user)
):
    idea = session.query(models.Idea).get(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    if idea.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your idea")

    session.delete(idea)
    session.commit()
    return {"detail": "Idea deleted"}


@router.post("/{idea_id}/clone", response_model=IdeaResponse)
def clone_idea(
    idea_id: str,
    session: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user)
):
    parent = session.query(models.Idea).get(idea_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Idea not found")

    # Respect privacy
    if parent.visibility == "private" and parent.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="This idea is private")

    if not parent.clonable:
        raise HTTPException(status_code=403, detail="Cloning not allowed for this idea")

    clone = models.Idea(
        user_id=current_user.id,
        title=f"Clone of {parent.title}",
        public_md=parent.public_md,
        private_md=parent.private_md,
        visibility="private",
        parent_id=parent.id,
        clonable=parent.clonable
    )
    session.add(clone)
    session.commit()
    session.refresh(clone)
    return clone
# ideas/main.py
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import hashlib, json

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
    share_hash: Optional[str] = None
    created_at: datetime
    owner_name: Optional[str] = None

    class Config:
        from_attributes = True  # enables model_validate(..., from_attributes=True)


# ==== Helpers ====
def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


def check_password(pw: str, hash_val: str) -> bool:
    return hash_password(pw) == hash_val


def generate_title(
    title: str,
    notes: Optional[str],
    links: Optional[List[str]],
    summary: Optional[str],
) -> str:
    """
    Ask LLM for a concise, compelling title (<= 60 chars), no quotes/punctuation at the end.
    """
    context = f"""
TITLE (user-supplied): {title or ""}
SUMMARY: {summary or ""}
NOTES: {notes or ""}
LINKS: {", ".join(links or [])}
""".strip()

    prompt = f"""
Craft a concise, compelling idea title (≤ 60 characters) from the context below.
Avoid trailing punctuation and do not wrap in quotes. Return ONLY the title text.

Context:
{context}
""".strip()

    t = ask_openai(prompt, "Generate idea title").strip()
    return t.replace("\n", " ").strip().strip('"').strip("'")


def generate_markdown_from_submission(
    title: str,
    notes: Optional[str],
    links: Optional[List[str]],
    summary: Optional[str],
):
    # Note: We pass the final title in "context" but instruct the model to avoid a top-level H1.
    context = f"""
TITLE
{title}

SUMMARY
{summary or ""}

NOTES
{notes or ""}

LINKS
{", ".join(links or [])}
"""

    public_prompt = f"""
Turn this into a clear, inspiring public-facing markdown page.

Rules:
- Do NOT include a top-level H1 title at the start (the app renders the title separately).
- Start with a short value-focused intro paragraph (no heading).
- Use section headings starting from '##' (H2) and below.
- Keep it crisp and scannable.

Source context:
{context}
""".strip()

    private_prompt = f"""
Turn this into exhaustive private notes for the creator.
- Include assumptions, risks, open questions, KPIs, draft milestones.
- Use markdown with '##' and lower. Avoid a top-level H1.

Source context:
{context}
""".strip()

    public_md = ask_openai(public_prompt, "Generate public markdown page")
    private_md = ask_openai(private_prompt, "Generate private markdown page")

    return public_md, private_md, context


def _safe_json_list(raw: str) -> List[str]:
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except Exception:
        pass
    # fallback to lines
    return [line.strip("-• ").strip() for line in raw.splitlines() if line.strip()]


def generate_clarifying_questions(title: str, public_md: str, private_md: str) -> List[str]:
    """
    Ask the LLM for 3–5 short clarifying questions to improve the page.
    """
    prompt = f"""
You are helping improve an idea page.

TITLE: {title}

PUBLIC MARKDOWN:
{public_md or ""}

PRIVATE MARKDOWN:
{private_md or ""}

Produce 3-5 short, specific clarifying questions that, if answered by the creator,
would materially improve the public page. Return ONLY a valid JSON list of strings.
Example:
["Who is the target audience?", "What is the key outcome?", "What timeline do you have?"]
""".strip()

    raw = ask_openai(prompt, "Generate clarifying questions")
    items = _safe_json_list(raw)
    return items[:5]


# ==== Routes ====

@router.post("/", response_model=IdeaResponse)
def create_idea(
    req: IdeaSubmission,
    session: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user),
):
    """Create a new idea from raw submission (AI generates title + markdown)"""
    # 1) Generate an improved title
    final_title = generate_title(req.title, req.notes, req.links, req.summary)

    # 2) Generate markdown that explicitly omits a top-level H1
    public_md, private_md, raw_context = generate_markdown_from_submission(
        final_title, req.notes, req.links, req.summary
    )

    password_hash = None
    if req.visibility == "password":
        if not req.password:
            raise HTTPException(status_code=400, detail="Password required for password-protected ideas")
        password_hash = hash_password(req.password)

    idea = models.Idea(
        user_id=current_user.id,
        title=final_title,           # <— use the improved title
        public_md=public_md,         # <— no H1 inside
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
        created_at=datetime.utcnow(),
    )
    session.add(raw_repo_item)
    session.commit()

    # 🔥 Seed unanswered clarifying questions (best-effort)
    try:
        qs = generate_clarifying_questions(idea.title, idea.public_md or "", idea.private_md or "")
        for q in qs:
            session.add(models.UnansweredQuestion(
                idea_id=idea.id,
                question=q,
                created_at=datetime.utcnow()
            ))
        session.commit()
    except Exception:
        # don't fail creation on LLM hiccups
        pass

    # Pydantic v2: model_validate + add owner_name
    return IdeaResponse.model_validate(idea, from_attributes=True).model_copy(
        update={"owner_name": current_user.name}
    )


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

    # attach owner_name
    owner = session.query(models.User).get(idea.user_id)
    owner_name = owner.name if owner else None

    return IdeaResponse.model_validate(idea, from_attributes=True).model_copy(
        update={"owner_name": owner_name}
    )


@router.patch("/{idea_id}", response_model=IdeaResponse)
def update_idea(
    idea_id: str,
    req: UpdateIdeaRequest,
    session: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user),
):
    idea = session.query(models.Idea).get(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    if idea.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your idea")

    if req.title is not None:
        idea.title = req.title
    if req.public_md is not None:
        idea.public_md = req.public_md
    if req.private_md is not None:
        idea.private_md = req.private_md
    if req.visibility is not None:
        idea.visibility = req.visibility
    if req.password is not None:
        idea.password_hash = hash_password(req.password)
    if req.clonable is not None:
        idea.clonable = req.clonable

    session.commit()
    session.refresh(idea)

    owner = session.query(models.User).get(idea.user_id)
    owner_name = owner.name if owner else None

    return IdeaResponse.model_validate(idea, from_attributes=True).model_copy(
        update={"owner_name": owner_name}
    )


@router.delete("/{idea_id}")
def delete_idea(
    idea_id: str,
    session: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user),
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
    current_user: models.User = Depends(get_current_user),
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
        clonable=parent.clonable,
    )
    session.add(clone)
    session.commit()
    session.refresh(clone)

    return IdeaResponse.model_validate(clone, from_attributes=True).model_copy(
        update={"owner_name": current_user.name}
    )
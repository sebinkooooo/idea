from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from datetime import datetime
import re

import db, models
from auth.main import get_current_user
from chat.openai_helper import ask_openai

router = APIRouter()

# ==== Schemas ====
class AskRequest(BaseModel):
    question: str

class AskResponse(BaseModel):
    answer: str
    images: List[Dict[str, Any]] = []
    references: List[Dict[str, Any]] = []
    # When the model proposes doc changes, include them (client may preview)
    updated_public_md: Optional[str] = None
    updated_private_md: Optional[str] = None

class AnswerUnansweredRequest(BaseModel):
    answer: str

class UpdateQARequest(BaseModel):
    answer: Optional[str] = None
    visibility: Optional[str] = None


# ==== Utils ====

SECTION_RE = re.compile(
    r"(?is)^\s*ANSWER:\s*(?P<answer>.*?)\n(?:UPDATED_PUBLIC_MD:\s*(?P<pub>.*?)\n)?(?:UPDATED_PRIVATE_MD:\s*(?P<priv>.*))?\s*$"
)

def parse_structured_answer(raw: str) -> Dict[str, Optional[str]]:
    """
    Expect the LLM to follow this template:

    ANSWER: <short direct reply>

    UPDATED_PUBLIC_MD:
    <full markdown or leave empty if no change>

    UPDATED_PRIVATE_MD:
    <full markdown or leave empty if no change>
    """
    m = SECTION_RE.match(raw.strip())
    if not m:
        # fallback: use entire response as answer
        return {"answer": raw.strip(), "pub": None, "priv": None}
    ans = (m.group("answer") or "").strip()
    pub = (m.group("pub") or "").strip() or None
    priv = (m.group("priv") or "").strip() or None
    return {"answer": ans, "pub": pub, "priv": priv}


# ==== Authenticated (owner) route: persists edits if owner ====

@router.post("/ideas/{idea_id}/ask", response_model=AskResponse)
def ask_idea(
    idea_id: str,
    req: AskRequest,
    session: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user)
):
    """Ask a question or request an edit (owner context). Model-proposed edits are persisted if caller is the owner."""
    idea = session.query(models.Idea).get(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")

    # Build recent Q&A history (last 5)
    history = (
        session.query(models.QAHistory)
        .filter(models.QAHistory.idea_id == idea.id)
        .order_by(models.QAHistory.created_at.desc())
        .limit(5)
        .all()
    )
    history_text = "\n".join(f"Q: {h.question}\nA: {h.answer}" for h in reversed(history))

    # Persistent QA (public or private) for owner
    qa_items = [
        f"Q: {r.name.replace('Q: ', '')}\nA: {r.content.replace('A: ', '')}"
        for r in idea.repo
        if r.type == "qa" and r.visibility in ["public", "private"]
    ]
    qa_items_text = "\n".join(qa_items)

    # Assets (public)
    assets_text = "\n".join(f"- {a.title}: {a.url}" for a in idea.assets if a.visibility == "public")

    # Owner context includes both public and private
    context = f"""
You are the *living document* for the idea below. You can:
1) Answer questions concisely.
2) If the user asks to make changes to the public or private markdown, you should output the FULL updated markdown.

IMPORTANT: Always respond in the *exact* template below.
- Put a short human-friendly reply under ANSWER.
- If no markdown changes are needed, leave those sections empty (blank).

TEMPLATE (do not add extra text outside these sections):

ANSWER:
<short answer to the user's message>

UPDATED_PUBLIC_MD:
<full markdown for the public page, or leave empty if no change>

UPDATED_PRIVATE_MD:
<full markdown for the private notes, or leave empty if no change>

# PUBLIC MARKDOWN
{idea.public_md or ""}

# PRIVATE MARKDOWN
{idea.private_md or ""}

# PERSISTENT QA
{qa_items_text}

# RECENT Q&A HISTORY
{history_text}

# PUBLIC ASSETS
{assets_text}
"""

    raw = ask_openai(context, req.question)
    if "[Chat unavailable]" in raw or "[Chat error" in raw:
        # record as unanswered so owner can resolve later
        uq = models.UnansweredQuestion(
            idea_id=idea.id, question=req.question, created_at=datetime.utcnow()
        )
        session.add(uq)
        session.commit()
        return AskResponse(answer="Sorry, chat is unavailable right now.", images=[], references=[])

    parsed = parse_structured_answer(raw)
    answer = parsed["answer"] or "OK."
    updated_public_md = parsed["pub"]
    updated_private_md = parsed["priv"]

    # Save QA history
    qa = models.QAHistory(
        idea_id=idea.id, question=req.question, answer=answer, created_at=datetime.utcnow()
    )
    session.add(qa)

    # If model says it can't answer, capture as unanswered
    if "i don't know" in answer.lower():
        session.add(models.UnansweredQuestion(
            idea_id=idea.id, question=req.question, created_at=datetime.utcnow()
        ))

    # Persist proposed edits only if caller owns the idea
    if (updated_public_md or updated_private_md) and current_user.id == idea.user_id:
        if updated_public_md:
            idea.public_md = updated_public_md
        if updated_private_md:
            idea.private_md = updated_private_md
        session.commit()  # commit both QA and edits in one go
    else:
        session.commit()

    return AskResponse(
        answer=answer,
        images=[{"title": a.title, "url": a.url} for a in idea.assets if a.visibility == "public"],
        references=[{"title": r.name, "url": r.url} for r in idea.repo if r.visibility == "public"],
        updated_public_md=updated_public_md,
        updated_private_md=updated_private_md,
    )


# ==== Public share route: no auth, no persistence of edits ====

@router.post("/share/{share_hash}/ask", response_model=AskResponse)
def ask_shared_idea(
    share_hash: str,
    req: AskRequest,
    password: Optional[str] = Query(None),
    session: Session = Depends(db.get_session),
):
    """
    Public chat about a shared idea (no sign-in required).
    - Respects visibility & optional password.
    - NEVER persists model-proposed edits; returns them for client-side preview only.
    - Does NOT expose private markdown or private QA in model context.
    """
    idea = session.query(models.Idea).filter(models.Idea.share_hash == share_hash).first()
    if not idea:
        raise HTTPException(status_code=404, detail="Shared idea not found")

    if idea.visibility == "private":
        raise HTTPException(status_code=403, detail="This idea is private and cannot be shared")

    if idea.visibility == "password":
        # Reuse the same helper as share GET endpoint (if you have one)
        from ideas.main import check_password  # lazy import to avoid circulars
        if not password or not check_password(password, idea.password_hash):
            raise HTTPException(status_code=403, detail="Password required or incorrect")

    # Build minimal, public-only context
    # Recent Q&A (only keep public facing answers if you tag them; otherwise keep short history or none)
    history = (
        session.query(models.QAHistory)
        .filter(models.QAHistory.idea_id == idea.id)
        .order_by(models.QAHistory.created_at.desc())
        .limit(3)
        .all()
    )
    history_text = "\n".join(f"Q: {h.question}\nA: {h.answer}" for h in reversed(history))

    # Persistent QA: ONLY public visibility
    qa_items = [
        f"Q: {r.name.replace('Q: ', '')}\nA: {r.content.replace('A: ', '')}"
        for r in idea.repo
        if r.type == "qa" and r.visibility == "public"
    ]
    qa_items_text = "\n".join(qa_items)

    # Public assets only
    assets_text = "\n".join(f"- {a.title}: {a.url}" for a in idea.assets if a.visibility == "public")

    # IMPORTANT: Do NOT include private_md here.
    context = f"""
You are the *public-facing* living document for the idea below.
Answer questions concisely. If the user requests edits to the public page,
you may propose the FULL updated public markdown. Do not include any private content.

IMPORTANT: Always respond in the *exact* template below.
If no markdown changes are needed, leave those sections empty (blank).

TEMPLATE (do not add extra text outside these sections):

ANSWER:
<short answer to the user's message>

UPDATED_PUBLIC_MD:
<full markdown for the public page, or leave empty if no change>

UPDATED_PRIVATE_MD:
<leave empty — not available in public context>

# PUBLIC MARKDOWN
{idea.public_md or ""}

# PUBLIC PERSISTENT QA
{qa_items_text}

# RECENT PUBLIC Q&A (short)
{history_text}

# PUBLIC ASSETS
{assets_text}
"""

    raw = ask_openai(context, req.question)
    if "[Chat unavailable]" in raw or "[Chat error" in raw:
        return AskResponse(answer="Sorry, chat is unavailable right now.", images=[], references=[])

    parsed = parse_structured_answer(raw)
    answer = parsed["answer"] or "OK."
    updated_public_md = parsed["pub"]

    # Optionally record the public interaction as QAHistory (without persisting edits)
    qa = models.QAHistory(
        idea_id=idea.id, question=req.question, answer=answer, created_at=datetime.utcnow()
    )
    session.add(qa)
    session.commit()

    return AskResponse(
        answer=answer,
        images=[{"title": a.title, "url": a.url} for a in idea.assets if a.visibility == "public"],
        references=[{"title": r.name, "url": r.url} for r in idea.repo if r.visibility == "public"],
        updated_public_md=updated_public_md,
        updated_private_md=None,  # never returned in public context
    )


# ==== Owner-only management routes (unchanged) ====

@router.get("/ideas/{idea_id}/unanswered")
def get_unanswered(
    idea_id: str,
    session: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user)
):
    """List unanswered questions for an idea (owner only)"""
    idea = session.query(models.Idea).get(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    if idea.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your idea")

    return idea.unanswered_questions


@router.post("/ideas/{idea_id}/unanswered/{uq_id}/answer")
def answer_unanswered(
    idea_id: str,
    uq_id: str,
    req: AnswerUnansweredRequest,
    session: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user)
):
    """Creator supplies an answer for a previously unanswered question"""
    idea = session.query(models.Idea).get(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    if idea.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your idea")

    uq = session.query(models.UnansweredQuestion).get(uq_id)
    if not uq or uq.idea_id != idea.id:
        raise HTTPException(status_code=404, detail="Unanswered question not found")

    # Add to QAHistory
    qa = models.QAHistory(
        idea_id=idea.id, question=uq.question, answer=req.answer, created_at=datetime.utcnow()
    )
    session.add(qa)

    # Add to Repo (persistent knowledge base)
    repo_item = models.RepoItem(
        idea_id=idea.id, name=f"Q: {uq.question}", type="qa",
        content=f"A: {req.answer}", visibility="private"
    )
    session.add(repo_item)

    # Remove unanswered
    session.delete(uq)
    session.commit()

    return {"detail": "Unanswered question resolved and added to knowledge base"}


@router.get("/ideas/{idea_id}/qa")
def list_persistent_qa(
    idea_id: str,
    session: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user)
):
    """List persistent Q&A (repo items) for an idea (owner only)"""
    idea = session.query(models.Idea).get(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    if idea.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your idea")
    return [r for r in idea.repo if r.type == "qa"]


@router.patch("/ideas/{idea_id}/qa/{qa_id}")
def update_persistent_qa(
    idea_id: str, qa_id: str, req: UpdateQARequest,
    session: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user)
):
    idea = session.query(models.Idea).get(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    if idea.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your idea")

    repo_item = session.query(models.RepoItem).get(qa_id)
    if not repo_item or repo_item.idea_id != idea.id or repo_item.type != "qa":
        raise HTTPException(status_code=404, detail="QA repo item not found")

    if req.answer:
        repo_item.content = f"A: {req.answer}"
    if req.visibility:
        repo_item.visibility = req.visibility

    session.commit()
    return {"detail": "Persistent QA updated"}


@router.delete("/ideas/{idea_id}/qa/{qa_id}")
def delete_persistent_qa(
    idea_id: str, qa_id: str,
    session: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user)
):
    idea = session.query(models.Idea).get(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    if idea.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your idea")

    repo_item = session.query(models.RepoItem).get(qa_id)
    if not repo_item or repo_item.idea_id != idea.id or repo_item.type != "qa":
        raise HTTPException(status_code=404, detail="QA repo item not found")

    session.delete(repo_item)
    session.commit()
    return {"detail": "Persistent QA deleted"}
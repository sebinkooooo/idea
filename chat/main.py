from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from datetime import datetime

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

class AnswerUnansweredRequest(BaseModel):
    answer: str

class UpdateQARequest(BaseModel):
    answer: Optional[str] = None
    visibility: Optional[str] = None


# ==== Routes ====

@router.post("/ideas/{idea_id}/ask", response_model=AskResponse)
def ask_idea(
    idea_id: str,
    req: AskRequest,
    session: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user)  # optional, could allow anon
):
    """Ask a question to an idea with short-term + persistent memory"""
    idea = session.query(models.Idea).get(idea_id)
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")

    # Build recent Q&A history (last 5 messages)
    history = (
        session.query(models.QAHistory)
        .filter(models.QAHistory.idea_id == idea.id)
        .order_by(models.QAHistory.created_at.desc())
        .limit(5)
        .all()
    )
    history_text = "\n".join(
        [f"Q: {h.question}\nA: {h.answer}" for h in reversed(history)]
    )

    # Collect persistent Q&A repo items
    qa_items = [
        f"Q: {r.name.replace('Q: ', '')}\nA: {r.content.replace('A: ', '')}"
        for r in idea.repo
        if r.type == "qa" and r.visibility in ["public", "private"]
    ]
    qa_items_text = "\n".join(qa_items)

    # Collect assets
    assets_text = "\n".join(
        [f"- {a.title}: {a.url}" for a in idea.assets if a.visibility == "public"]
    )

    # Build context
    context = f"""
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

    # Call LLM
    raw_answer = ask_openai(context, req.question)

    # If model can't answer → log in unanswered
    if "i don't know" in raw_answer.lower() or "chat unavailable" in raw_answer.lower():
        unanswered = models.UnansweredQuestion(
            idea_id=idea.id,
            question=req.question,
            created_at=datetime.utcnow()
        )
        session.add(unanswered)
        session.commit()
        return AskResponse(answer=raw_answer, images=[], references=[])

    # Otherwise → normal QAHistory
    qa = models.QAHistory(
        idea_id=idea.id,
        question=req.question,
        answer=raw_answer,
        created_at=datetime.utcnow()
    )
    session.add(qa)
    session.commit()

    # Build response
    return AskResponse(
        answer=raw_answer,
        images=[
            {"title": a.title, "url": a.url}
            for a in idea.assets if a.visibility == "public"
        ],
        references=[
            {"title": r.name, "url": r.url}
            for r in idea.repo if r.visibility == "public"
        ]
    )


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
        idea_id=idea.id,
        question=uq.question,
        answer=req.answer,
        created_at=datetime.utcnow()
    )
    session.add(qa)

    # Add to Repo (persistent knowledge base)
    repo_item = models.RepoItem(
        idea_id=idea.id,
        name=f"Q: {uq.question}",
        type="qa",
        content=f"A: {req.answer}",
        visibility="private"
    )
    session.add(repo_item)

    # Remove unanswered
    session.delete(uq)
    session.commit()

    return {"detail": "Unanswered question resolved and added to knowledge base"}


# ==== Persistent QA management ====

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
    idea_id: str,
    qa_id: str,
    req: UpdateQARequest,
    session: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user)
):
    """Update a persistent QA entry (owner only)"""
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
    idea_id: str,
    qa_id: str,
    session: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user)
):
    """Delete a persistent QA entry (owner only)"""
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
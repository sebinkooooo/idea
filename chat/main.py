# chat/main.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from datetime import datetime
import json

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
    # ✅ new but backwards compatible
    updated_public_md: Optional[str] = None
    updated_private_md: Optional[str] = None
    updated_fields: List[str] = []
    questions_for_user: List[str] = []

class AnswerUnansweredRequest(BaseModel):
    answer: str

class UpdateQARequest(BaseModel):
    answer: Optional[str] = None
    visibility: Optional[str] = None


def _editing_prompt(context: str, user_question: str) -> str:
    return f"""
You are an expert product-writing and editing assistant.

USER REQUEST:
{user_question}

TASK:
1) If the user is asking to change the page, produce revised PUBLIC markdown in `updated_public_md` (or null).
2) If the request concerns private notes, produce revised PRIVATE markdown in `updated_private_md` (or null).
3) Always provide a helpful natural-language `answer`.
4) If you need more info, include up to 3 short clarifying questions in `questions_for_user`.

Return ONLY valid JSON with this exact shape:
{{
  "answer": "string",
  "updated_public_md": "string or null",
  "updated_private_md": "string or null",
  "questions_for_user": ["string", "string"]
}}

CONTEXT:
{context}
""".strip()

def _safe_parse_llm_json(raw: str) -> Dict[str, Any]:
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Not an object")
    except Exception:
        # fall back to plain text answer
        return {
            "answer": raw if isinstance(raw, str) else "",
            "updated_public_md": None,
            "updated_private_md": None,
            "questions_for_user": [],
        }

    # fill defaults
    data.setdefault("answer", "")
    data.setdefault("updated_public_md", None)
    data.setdefault("updated_private_md", None)
    q = data.get("questions_for_user", [])
    if not isinstance(q, list):
        q = []
    data["questions_for_user"] = [str(s).strip() for s in q if str(s).strip()]
    return data


# ==== Routes ====

@router.post("/ideas/{idea_id}/ask", response_model=AskResponse)
def ask_idea(
    idea_id: str,
    req: AskRequest,
    session: Session = Depends(db.get_session),
    current_user: models.User = Depends(get_current_user)  # keep auth
):
    """Ask a question to an idea. May edit markdown + add follow-up questions."""
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

    # Persistent Q&A repo items
    qa_items = [
        f"Q: {r.name.replace('Q: ', '')}\nA: {r.content.replace('A: ', '')}"
        for r in idea.repo
        if r.type == "qa" and r.visibility in ["public", "private"]
    ]
    qa_items_text = "\n".join(qa_items)

    # Public assets
    assets_text = "\n".join(
        [f"- {a.title}: {a.url}" for a in idea.assets if a.visibility == "public"]
    )

    # Full context
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
""".strip()

    # Ask LLM for structured JSON (but we still accept plain text fallback)
    raw = ask_openai(context=_editing_prompt(context, req.question), question="Propose edits + answer")

    data = _safe_parse_llm_json(raw)
    answer = data.get("answer") or ""
    updated_public_md = data.get("updated_public_md")
    updated_private_md = data.get("updated_private_md")
    followups: List[str] = data.get("questions_for_user", [])

    updated_fields: List[str] = []

    # If the model couldn't answer, log to unanswered and reply as-is
    if "i don't know" in answer.lower() or "chat unavailable" in answer.lower():
        session.add(models.UnansweredQuestion(
            idea_id=idea.id,
            question=req.question,
            created_at=datetime.utcnow()
        ))
        session.commit()
        return AskResponse(answer=answer, images=[], references=[], questions_for_user=followups)

    # Apply edits if present
    if isinstance(updated_public_md, str) and updated_public_md.strip():
        idea.public_md = updated_public_md.strip()
        updated_fields.append("public_md")
    if isinstance(updated_private_md, str) and updated_private_md.strip():
        idea.private_md = updated_private_md.strip()
        updated_fields.append("private_md")

    session.commit()

    # Save QA history
    qa_row = models.QAHistory(
        idea_id=idea.id,
        question=req.question,
        answer=answer,
        created_at=datetime.utcnow()
    )
    session.add(qa_row)

    # Persist follow-up questions (unanswered)
    if followups:
        for q in followups[:3]:
            if q.strip():
                session.add(models.UnansweredQuestion(
                    idea_id=idea.id,
                    question=q.strip(),
                    created_at=datetime.utcnow()
                ))

    session.commit()

    # Build response
    return AskResponse(
        answer=answer,
        images=[{"title": a.title, "url": a.url} for a in idea.assets if a.visibility == "public"],
        references=[{"title": r.name, "url": r.url} for r in idea.repo if r.visibility == "public"],
        updated_public_md=updated_public_md if "public_md" in updated_fields else None,
        updated_private_md=updated_private_md if "private_md" in updated_fields else None,
        updated_fields=updated_fields,
        questions_for_user=followups,
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
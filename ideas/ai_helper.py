# backend/ideas/ai_helper.py
from typing import List, Optional
from backend.chat.openai_helper import ask_openai

def generate_title(title: str, notes: Optional[str], links: Optional[List[str]], summary: Optional[str]) -> str:
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

def generate_markdown_from_submission(final_title: str, notes: Optional[str], links: Optional[List[str]], summary: Optional[str]):
    context = f"""
TITLE
{final_title}

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
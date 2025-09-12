# backend/ideas/ai_helper.py
from typing import List, Optional
from chat.openai_helper import ask_openai

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

import pathlib

PROMPT_DIR = pathlib.Path(__file__).parent / "prompts"

def _load_prompt(name: str) -> str:
    path = PROMPT_DIR / name
    return path.read_text(encoding="utf-8")

def generate_markdown_from_submission(
    title: str,
    notes: Optional[str],
    links: Optional[List[str]],
    summary: Optional[str],
):
    context = f"""
TITLE
{title}

SUMMARY
{summary or ""}

NOTES
{notes or ""}

LINKS
{", ".join(links or [])}
""".strip()

    # Load prompt templates
    public_template = _load_prompt("public_markdown.md")
    private_template = _load_prompt("private_markdown.md")

    # Fill in context
    public_prompt = public_template.replace("{{context}}", context)
    private_prompt = private_template.replace("{{context}}", context)

    public_md = ask_openai(public_prompt, "Generate public markdown v1")
    private_md = ask_openai(private_prompt, "Generate private markdown v1")

    return public_md.strip(), private_md.strip(), context
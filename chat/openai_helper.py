import os
from openai import OpenAI

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

def ask_openai(context: str, question: str) -> str:
    """Ask OpenAI with context + question, return plain answer text"""
    if not client:
        return "[Chat unavailable]"

    prompt = f"""
You are the interactive version of an idea (a living document).
Answer truthfully and concisely. If you don't know, say so.

# CONTEXT
{context}

# QUESTION
{question}
"""
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are a helpful, concise assistant."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=400,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"[Chat error: {e}]"
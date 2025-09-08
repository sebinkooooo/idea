from backend.chat.openai_helper import ask_openai

def generate_markdown_from_submission(title: str, notes: str, links: list, summary: str):
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

    return public_md, private_md
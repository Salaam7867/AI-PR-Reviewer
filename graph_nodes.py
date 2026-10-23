
import os
from pathlib import Path
import tempfile
from dotenv import load_dotenv
from groq import Groq

from graph_state import ReviewState
from linter_tool import run_linter
from review_schema import CodeReview


load_dotenv()

api_key = os.getenv("GROQ_API_KEY")

if not api_key:
    raise ValueError("GROQ_API_KEY is missing from .env")

client = Groq(api_key=api_key)


def linter_node(state: ReviewState) -> dict:
    source_code = state["source_code"]

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        encoding="utf-8",
        delete=False,
    ) as temp_file:
        temp_file.write(source_code)
        temp_path = temp_file.name

    try:
        lint_report = run_linter(temp_path)
    finally:
        Path(temp_path).unlink(missing_ok=True)

    return {"lint_report": lint_report}



def reviewer_node(state: ReviewState) -> dict:
    source_code = state["source_code"]
    lint_report = state["lint_report"]
    # You will need to add git_diff to your ReviewState typed dict
    git_diff = state.get("git_diff", "No diff provided.") 

    prompt = f"""
        You are a Pull Request Reviewer. 

        Your strict task is to identify Bugs, Security risks, and Code Hygiene issues ONLY in the newly modified code.
        
        Rules:
        1. ONLY critique lines that were added or modified in the GIT DIFF (lines starting with +).
        2. Do NOT report issues in existing legacy code that was not changed by this PR.
        3. Use the FULL PYTHON CODE purely as background context to understand how the new changes interact with the rest of the file.
        4. For every finding, provide a specific suggested_fix when a safe, actionable fix is possible.
        5. Return findings using the required JSON schema.

        GIT DIFF (The actual changes to review):
        {git_diff}

        FULL PYTHON CODE (For background context only):
        {source_code}

        FLAKE8 REPORT (Syntactical errors):
        {lint_report}
        """

    response = client.chat.completions.create(
        model="gpt-oss-120b", # Note: gpt-oss-120b will throw an error on Groq
        messages=[
            {
                "role": "system",
                "content": (
                    "Return a valid JSON object with exactly three keys: bugs, security, and code_hygiene. "
                    "Each key must contain an array. Each finding must contain description (string), "
                    "location (string), severity (one of low, medium, high, potential), and suggested_fix (string or null). "
                    "Use empty arrays when there are no findings. Return raw JSON only."
                )
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
    )

    review_json = response.choices[0].message.content

    if not review_json:
        raise ValueError("Groq returned an empty review.")

    validated_review = CodeReview.model_validate_json(review_json)

    return {"ai_review": validated_review}



def fixer_node(state: ReviewState) -> dict:
    source_code = state["source_code"]
    ai_review = state["ai_review"]

    if ai_review is None:
        raise ValueError("AI review is missing; cannot fix code.")

    review_json = ai_review.model_dump_json(indent=2)

    prompt = f"""
    You are a careful Python code repair agent.

    Revise the provided Python source code using the review findings.

    Rules:
    - Return the complete revised Python source code only.
    - Do not return Markdown fences or explanations.
    - Make minimal changes that address valid findings.
    - Preserve the program's intended behavior.
    - Do not implement ambiguous behavior changes without justification.
    - Do not make unrelated refactors.
    - If a finding is only a potential risk and its correct behavior is
    unclear, leave it unchanged.
    - Preserve existing functionality and imports unless a change is needed.

    REVIEW FINDINGS:
    {review_json}

    CURRENT PYTHON SOURCE:
    {source_code}
    """

    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {
                "role": "system",
                "content": (
                    "You repair Python code. Return only the complete "
                    "revised source code, without Markdown fences."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
    )

    fixed_code = response.choices[0].message.content

    if not fixed_code or not fixed_code.strip():
        raise ValueError("Groq returned empty fixed code.")

    fixed_code = fixed_code.strip() + "\n"

    # Reject Markdown-wrapped output instead of saving it as Python.
    if fixed_code.startswith("```"):
        raise ValueError(
            "Groq returned Markdown instead of plain Python source."
        )

    return {
        "source_code": fixed_code,
        "fix_attempts": state["fix_attempts"] + 1,
    }





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

    
    prompt = f"""
        Review the following Python code and its Flake8 report.

        Identify issues in these categories:
        1. Bugs
        2. Security
        3. Code hygiene

        For every finding:
        - Describe the issue accurately.
        - Give its location in the source code.
        - Assign severity: low, medium, high, or potential.
        - Provide a specific suggested_fix when a safe, actionable fix is possible.
        - If no safe fix is appropriate, set suggested_fix to null.

        Rules:
        - Do not invent issues unsupported by the code.
        - Distinguish confirmed bugs from potential risks.
        - Do not treat style preferences as bugs.
        - Avoid duplicate findings.
        - Prefer minimal changes that preserve existing behavior.
        - Do not claim a fix is tested or verified.

        Return findings using the required JSON schema.

        PYTHON CODE:
        {source_code}

        FLAKE8 REPORT:
        {lint_report}
        """


    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
            
    messages=[
        {
            "role": "system",
            "content": 
"Return a valid JSON object with exactly three keys: bugs, security, and code_hygiene. Each key must contain an array. Each finding must contain description (string), location (string), severity (one of low, medium, high, potential), and suggested_fix (string or null). Use empty arrays when there are no findings. Return JSON only."

        },
        {"role": "user", "content": prompt},
    ],

            temperature=0.2,
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




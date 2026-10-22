
import os
from pathlib import Path

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
    file_path = state["file_path"]
    lint_report = run_linter(file_path)

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
        model="openai/gpt-oss-20b",
        
messages=[
    {
        "role": "system",
        "content": """
Return only a valid JSON object with exactly these top-level keys:
bugs, security, code_hygiene.

Each key must contain an array of findings.

Each finding must have:
- description: string
- location: string
- severity: one of "low", "medium", "high", "potential"
- suggested_fix: string or null

If a category has no findings, return an empty array.
Do not include Markdown fences or text outside the JSON object.
"""
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

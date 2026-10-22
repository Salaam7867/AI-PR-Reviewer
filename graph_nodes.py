
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

Identify potential issues in these categories:
1. Bugs
2. Security
3. Code hygiene

Do not invent issues unsupported by the code.
Distinguish confirmed problems from potential risks.

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
                "content": (
                    "You are a Python code reviewer. Return a JSON "
                    "object with bugs, security, and code_hygiene "
                    "arrays. Each finding must contain description, "
                    "location, and severity. Severity must be "
                    "low, medium, high, or potential."
                ),
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

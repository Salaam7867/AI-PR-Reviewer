import os
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq
from review_schema import CodeReview
from linter_tool import run_linter

load_dotenv()

api_key = os.getenv("GROQ_API_KEY")

if not api_key:
    raise ValueError("GROQ_API_KEY is missing from .env")

client = Groq(api_key=api_key)


def review_code(file_path: str) -> str:
    code = Path(file_path).read_text(encoding="utf-8")
    lint_report = run_linter(file_path)

    prompt = f"""
You are a Python code reviewer.

Review the Python code and its Flake8 report.

Categorize your findings into:
1. Bugs
2. Security
3. Code Hygiene

Distinguish confirmed problems from potential risks.
Do not invent issues unsupported by the code.

Return valid JSON with these keys:
- bugs
- security
- code_hygiene

PYTHON CODE:
{code}

FLAKE8 REPORT:
{lint_report}
"""

    response = client.chat.completions.create(
        model="openai/gpt-oss-20b",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a Python code reviewer. "
                    "Return a JSON object with bugs, security, "
                    "and code_hygiene arrays. Each finding must "
                    "contain description, location, and severity. "
                    "Severity must be low, medium, high, or potential."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )

    review_json = response.choices[0].message.content

    validated_review = CodeReview.model_validate_json(review_json)

    return validated_review.model_dump_json(indent=2)


if __name__ == "__main__":
    result = review_code("sample.py")
    print("\nAI CODE REVIEW")
    print("=" * 50)
    print(result)
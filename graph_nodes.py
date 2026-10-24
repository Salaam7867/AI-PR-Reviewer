
import os
import re
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


def get_modified_lines(git_diff: str) -> set:
    """Parses a git diff patch and returns a set of newly added/modified line numbers."""
    modified_lines = set()
    if not git_diff or git_diff == "No diff available.":
        return modified_lines
        
    current_line = 0
    for line in git_diff.splitlines():
        if line.startswith("@@"):
            # Extract the starting line number for the new file chunk (e.g., @@ -10,5 +15,6 @@)
            match = re.search(r"\+(\d+)", line)
            if match:
                current_line = int(match.group(1))
        elif line.startswith("+++") or line.startswith("---"):
            continue
        elif line.startswith("+"):
            # This is a new/modified line in the PR
            modified_lines.add(current_line)
            current_line += 1
        elif line.startswith(" "):
            # This is an unchanged context line
            current_line += 1
            
    return modified_lines


def filter_lint_report(lint_report: str, modified_lines: set) -> str:
    """Filters the Flake8 report to only include errors on modified lines."""
    if not modified_lines:
        return "No issues found in the PR diff."
        
    filtered_errors = []
    for issue in lint_report.strip().splitlines():
        # Flake8 outputs errors in the format: filename.py:LINE:COL: ERROR_CODE details
        match = re.search(r":(\d+):", issue)
        if match:
            line_num = int(match.group(1))
            if line_num in modified_lines:
                filtered_errors.append(issue)
                
    return "\n".join(filtered_errors) if filtered_errors else "No issues found in the PR diff."


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

    git_diff = state.get("git_diff", "")
    
    # ... (your existing tempfile and run_linter logic stays the same) ...

    # Parse the diff to find which lines the PR actually changed
    modified_lines = get_modified_lines(git_diff)
    
    # Filter the linter report so we only complain about the PR's lines
    targeted_lint_report = filter_lint_report(lint_report, modified_lines)

    return {"lint_report": targeted_lint_report}




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

    CRITICAL RULES:
    1. You MUST return the ENTIRE, complete Python source code from start to finish.
    2. DO NOT use placeholders like "# ... rest of code unchanged ...".
    3. DO NOT omit any imports, classes, or functions. The output must be a fully functional, complete file.
    4. Only modify the specific lines required to address the valid findings in the JSON.
    5. Return ONLY the raw Python code. No markdown fences, no explanations.

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




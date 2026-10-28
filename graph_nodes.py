import os
import re
import ast
import difflib
from pathlib import Path
import tempfile
from dotenv import load_dotenv
from groq import Groq
import logging
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception, before_sleep_log


from graph_state import ReviewState
from linter_tool import run_linter
from review_schema import CodeReview

from dotenv import load_dotenv
load_dotenv(override=True) # Forces Python to read the new key from the file

api_key = os.getenv("GROQ_API_KEY")
if not api_key:
    raise ValueError("GROQ_API_KEY is missing from environment variables")
# Setup the logger so tenacity knows where to print the errors
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("GroqRetry")

def is_transient_error(exception):
    error_str = str(exception).lower()
    # Only retry actual transient network issues or rate limits
    if "429" in error_str or "rate limit" in error_str or "503" in error_str:
        return True 
    return False # FIX: Immediately fail on permanent errors like 400 Bad Request

@retry(
    retry=retry_if_exception(is_transient_error),
    wait=wait_exponential(multiplier=2, min=2, max=10),
    stop=stop_after_attempt(4),
    reraise=True, # FIX: Strip the Tenacity wrapper and print the actual Groq error to Streamlit
    before_sleep=before_sleep_log(logger, logging.WARNING)
)
def safe_groq_call(prompt: str, is_json: bool = False) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY is missing from environment variables")
        
    client = Groq(api_key=api_key)
    
    if is_json and "json" not in prompt.lower():
        prompt += "\n\nCRITICAL: You must return valid JSON."

    completion = client.chat.completions.create(
        model="openai/gpt-oss-120b", # FIX: Upgraded from 8k to 128k context window
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        response_format={"type": "json_object"} if is_json else None
    )
    
    return completion.choices[0].message.content


def get_modified_lines(git_diff: str) -> set:
    modified_lines = set()
    if not git_diff or git_diff == "No diff available.":
        return modified_lines
        
    current_line = 0
    for line in git_diff.splitlines():
        if line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            if match:
                current_line = int(match.group(1))
        elif line.startswith("+++") or line.startswith("---"):
            continue
        elif line.startswith("+"):
            modified_lines.add(current_line)
            current_line += 1
        elif line.startswith(" "):
            current_line += 1
            
    return modified_lines

def filter_lint_report(lint_report: str, modified_lines: set) -> str:
    if not modified_lines:
        return "Unable to filter lint findings: no modified lines were identified."
        
    filtered_errors = []
    for issue in lint_report.strip().splitlines():
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
    modified_lines = get_modified_lines(git_diff)
    targeted_lint_report = filter_lint_report(lint_report, modified_lines)

    return {"lint_report": targeted_lint_report}

def filter_out_of_scope_findings(
    findings: list,
    valid_lines: set[int]
) -> list:
    """Keep findings only when their location identifies a valid changed line."""
    valid_findings = []

    for finding in findings:
        location = finding.location.strip()

        # Accept explicit formats such as "Line 37" or "file.py:37"
        match = re.search(
            r"(?:\bline\s+|:(\d+)\b)",
            location,
            re.IGNORECASE
        )

        if not match:
            # Do not silently accept an ambiguous location.
            print(
                f"Guardrail: Dropped finding with ambiguous location: "
                f"{location!r}"
            )
            continue

        line_num = int(
            match.group(1) if match.group(1) else
            re.search(r"\bline\s+(\d+)", location, re.IGNORECASE).group(1)
        )

        if line_num in valid_lines:
            valid_findings.append(finding)
        else:
            print(
                f"Guardrail: Dropped out-of-scope finding at line {line_num}"
            )

    return valid_findings

def get_valid_changed_lines(git_diff: str) -> set[int]:
    """Return line numbers added or modified in the new file."""
    valid_lines = set()
    current_line_num = -1

    for line in git_diff.splitlines():
        if line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            current_line_num = int(match.group(1)) if match else -1

        elif line.startswith(("+++", "---")):
            continue

        elif line.startswith("+"):
            if current_line_num != -1:
                valid_lines.add(current_line_num)
                current_line_num += 1

        elif line.startswith("-"):
            continue

        elif current_line_num != -1:
            current_line_num += 1

    return valid_lines

def reviewer_node(state: dict) -> dict:
    source_code = state["source_code"]
    git_diff = state["git_diff"]
    lint_report = state["lint_report"]

    # Extract only the added lines using splitlines() to create a strict focus filter
    added_lines = "\n".join([
        line for line in git_diff.splitlines() 
        if line.startswith('+') and not line.startswith('+++')
    ])
    
    if not added_lines.strip():
        added_lines = ""

    prompt = f"""
        You are a strict, highly focused Pull Request Reviewer. 

        <read_only_background_context>
        {source_code}
        </read_only_background_context>

        <syntactical_flake8_errors>
        {lint_report}
        </syntactical_flake8_errors>

        <actual_pr_changes_to_review>
        {git_diff}
        </actual_pr_changes_to_review>

        <strictly_added_lines>
        {added_lines}
        </strictly_added_lines>

        CRITICAL RULES:
        1. Report findings only when the defect is demonstrably caused by, or introduced by, the changed code. 
        2. Use the complete <actual_pr_changes_to_review> diff to understand modifications and extract accurate line numbers from the hunk headers (e.g., @@ -10,2 +10,3 @@).
        3. Ignore unrelated pre-existing defects in the <read_only_background_context>. 
        4. A finding's location MUST refer to a valid added or modified line in the new version of the file, formatted strictly as either "Line X" or "filename.py:X". Do not use ambiguous descriptions.
        5. If <strictly_added_lines> is empty, inspect the full diff for deletions that may introduce bugs or security vulnerabilities. Report a finding only if it can be anchored to a valid added or modified line in the new file. If a deletion-only change introduces a potential defect but cannot be anchored to a valid new-file line, do not invent a line number or force it into the findings; leave it for manual review.
        6. Return a valid JSON object with exactly three keys: bugs, security, and code_hygiene. Each finding must include description, location, severity, and suggested_fix. If no safe fix can be provided, leave suggested_fix null or empty.
        """
    
    # ... keep your existing try/except block for safe_groq_call and Pydantic parsing below this

    review_json = safe_groq_call(prompt, is_json=True)

    if not review_json:
        raise ValueError(
            "Groq returned an empty review. Halting pipeline "
            "to prevent false passes."
        )

    validated_review = CodeReview.model_validate_json(review_json)

    valid_lines_set = get_valid_changed_lines(git_diff)

    validated_review.bugs = filter_out_of_scope_findings(
        validated_review.bugs, valid_lines_set
    )
    validated_review.security = filter_out_of_scope_findings(
        validated_review.security, valid_lines_set
    )
    validated_review.code_hygiene = filter_out_of_scope_findings(
        validated_review.code_hygiene, valid_lines_set
    )

    return {"ai_review": validated_review}


def is_fix_in_scope(original_code: str, fixed_code: str, valid_lines: set[int]) -> bool:
    """
    Ensures the AI's proposed fix only modifies code strictly at or adjacent to the original PR changes.
    """
    if not valid_lines:
        print("🛡️ Guardrail: Rejected. No valid lines provided (fail closed).")
        return False 
        
    matcher = difflib.SequenceMatcher(None, original_code.splitlines(), fixed_code.splitlines())
    
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != 'equal':
            if tag == 'insert':
                # difflib uses 0-based indexing; i1 + 1 is the 1-based line number where code is inserted
                insertion_point = i1 + 1
                
                # An insertion is only valid if it occurs exactly at or adjacent to a PR-modified line
                if not any(abs(insertion_point - v) <= 1 for v in valid_lines):
                    print(f"🛡️ Guardrail Blocked: 'insert' operation at line {insertion_point} is out of scope.")
                    return False
            else:
                # For replace and delete, EVERY modified line must be strictly within the valid_lines set
                changed_lines = set(range(i1 + 1, i2 + 1))
                if not changed_lines.issubset(valid_lines):
                    out_of_bounds = changed_lines - valid_lines
                    print(f"🛡️ Guardrail Blocked: '{tag}' operation modified out-of-scope lines {out_of_bounds}.")
                    return False
                    
    return True

def fixer_node(state: ReviewState) -> dict:
    source_code = state["source_code"]
    ai_review = state["ai_review"]
    git_diff = state.get("git_diff", "") # ADD THIS LINE TO FETCH git_diff FROM STATE

    if not ai_review or not (ai_review.bugs or ai_review.security or ai_review.code_hygiene):
        return {"source_code": source_code, "fix_attempts": state["fix_attempts"]}

    review_json = ai_review.model_dump_json(indent=2)
    valid_lines_set = get_valid_changed_lines(git_diff)

    prompt = f"""
You are a careful Python code repair agent working on a GitHub Pull Request (PR).

Your task is to repair only the problems identified in the review findings.
The goal is to fix the PR's reported issues without changing unrelated code.

PR DIFF:
{git_diff}

PERMITTED LINE NUMBERS IN THE CURRENT PR FILE:
{sorted(valid_lines_set)}

CRITICAL RULES:

1. COMPLETE FILE:
   Return the entire Python source file from start to finish.
   Preserve every import, class, function, comment, and other section
   that does not need to change for the requested repair.
   Never use placeholders or omit unchanged code.

2. SCOPE POLICY:
- Treat the permitted line numbers as the only authorised repair region.
- Use the PR diff to understand the developer's changes.
- Fix only the supplied findings that correspond to the permitted region.
- Preserve all other code, including adjacent lines, exactly as provided.
- Do not add separate statements, logging, helper functions, or validation
  merely because they are near a permitted line.
- A multi-line replacement is allowed only when the entire replacement
  is an authorised repair of the permitted statement or block.
- Do not modify any other function or unrelated statement.
- If a finding cannot be fixed within the permitted region, do not
  expand the scope yourself.

3. INSERTIONS AND MULTI-LINE FIXES:
   You may replace a permitted statement or code block with multiple
   lines when those lines are necessary to resolve a supplied finding.
   Do not add separate statements merely because they are adjacent to
   a permitted line.
   Do not modify another function or unrelated statement.
   Preserve all unrelated source code exactly as provided.

4. REVIEW FINDINGS:
   Use the supplied findings as the authority for what needs fixing.
   If a finding does not identify a clear, actionable problem, do not
   invent a repair for it.
   Do not make changes beyond what is needed to resolve the findings.

5. PRESERVE BEHAVIOUR:
   Preserve the existing behaviour of unrelated code.
   Do not remove existing functionality or introduce unrelated changes.
   Ensure the repaired code remains syntactically valid Python.

6. OUTPUT:
   Return ONLY the complete raw Python source code.
   Do not include Markdown fences, explanations, or commentary.

REVIEW FINDINGS (JSON):
{review_json}

CURRENT PYTHON SOURCE:
{source_code}
"""


    try:
        fixed_code = safe_groq_call(prompt, is_json=False)
        
        if fixed_code.startswith("```python"):
            fixed_code = fixed_code[9:]
        if fixed_code.startswith("```"):
            fixed_code = fixed_code[3:]
        if fixed_code.endswith("```"):
            fixed_code = fixed_code[:-3]

        fixed_code = fixed_code.strip() + "\n"
        
        # 🛡️ Guardrail 1: AST Validation
        try:
            ast.parse(fixed_code)
        except SyntaxError as e:
            print(f"AST Validation Failed: AI generated invalid Python syntax - {e}")
            return {"source_code": source_code, "fix_attempts": state["fix_attempts"] + 1}
            
        # 🛡️ Guardrail 2: Scope Validation
        if not is_fix_in_scope(source_code, fixed_code, valid_lines_set):
            print("Fix rejected: AI attempted to modify unrelated code.")
            fixed_code = source_code

    except Exception as e:
        print(f"Error in Fixer Node: {e}")
        fixed_code = source_code

    return {
        "source_code": fixed_code,
        "fix_attempts": state["fix_attempts"] + 1,
    }
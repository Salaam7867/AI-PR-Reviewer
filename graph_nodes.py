import os
import re
import ast
from pathlib import Path
import tempfile
from dotenv import load_dotenv
from google import genai
from google.genai import types
import logging
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception, before_sleep_log

from google.genai.errors import APIError

from graph_state import ReviewState
from linter_tool import run_linter
from review_schema import CodeReview

from dotenv import load_dotenv
load_dotenv(override=True) # Forces Python to read the new key from the file

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY is missing from .env")

# Setup the logger so tenacity knows where to print the errors
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("GeminiRetry")

def is_transient_error(exception):
    # If it's a Google API error and the code is 429 (Quota), DO NOT retry.
    if isinstance(exception, APIError) and exception.code == 429:
        return False
    return True # Retry everything else

@retry(
    retry=retry_if_exception(is_transient_error),
    wait=wait_exponential(multiplier=2, min=2, max=10),
    stop=stop_after_attempt(3),
    before_sleep=before_sleep_log(logger, logging.WARNING)
)
def safe_gemini_call(prompt: str, is_json: bool = False) -> str:
    # The new SDK uses a centralized Client object
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    
    # Configuration is now handled via types.GenerateContentConfig
    config = types.GenerateContentConfig(temperature=0.1)
    if is_json:
        config.response_mime_type = "application/json"
        
    # generate_content is now called through client.models
    response = client.models.generate_content(
        model='gemini-3.8-flash', 
        contents=prompt,
        config=config
    )
    return response.text
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

def reviewer_node(state: ReviewState) -> dict:
    source_code = state["source_code"]
    lint_report = state["lint_report"]
    git_diff = state.get("git_diff", "No diff provided.") 

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

        CRITICAL RULES:
        1. YOU MUST ONLY CRITIQUE THE NEW CHANGES IN <actual_pr_changes_to_review>.
        2. Specifically, only review lines starting with a plus sign (+).
        3. If you suggest a fix for legacy code from <read_only_background_context> that was not touched by this PR, you have failed.
        4. Use the background context purely to understand how the PR changes fit into the wider file. Do not review it.
        5. Return a valid JSON object with exactly three keys: bugs, security, and code_hygiene. Each key must contain an array of findings with description, location, severity, and suggested_fix.
        """

    review_json = safe_gemini_call(prompt, is_json=True)
    if not review_json:
        raise ValueError("Gemini returned an empty review. Halting pipeline to prevent false passes.")

    validated_review = CodeReview.model_validate_json(review_json)
    return {"ai_review": validated_review}

def fixer_node(state: ReviewState) -> dict:
    source_code = state["source_code"]
    ai_review = state["ai_review"]

    if not ai_review or not (ai_review.bugs or ai_review.security or ai_review.code_hygiene):
        return {"source_code": source_code, "fix_attempts": state["fix_attempts"]}

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

    try:
        fixed_code = safe_gemini_call(prompt, is_json=False)
        
        if fixed_code.startswith("```python"):
            fixed_code = fixed_code[9:]
        if fixed_code.startswith("```"):
            fixed_code = fixed_code[3:]
        if fixed_code.endswith("```"):
            fixed_code = fixed_code[:-3]

        fixed_code = fixed_code.strip() + "\n"
        
        # AST Validation: Ensure the AI didn't write broken Python syntax
        try:
            ast.parse(fixed_code)
        except SyntaxError as e:
            print(f"AST Validation Failed: AI generated invalid Python syntax - {e}")
            fixed_code = source_code # Revert to original if syntax is broken
            
    except Exception as e:
        print(f"Error in Fixer Node: {e}")
        fixed_code = source_code

    return {
        "source_code": fixed_code,
        "fix_attempts": state["fix_attempts"] + 1,
    }
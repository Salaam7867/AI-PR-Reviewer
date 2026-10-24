import os
import re
import ast
from pathlib import Path
import tempfile
from dotenv import load_dotenv
from google import genai
from google.genai import types
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

from graph_state import ReviewState
from linter_tool import run_linter
from review_schema import CodeReview

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise ValueError("GEMINI_API_KEY is missing from .env")


@retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(multiplier=2, min=4, max=60), # Wait 4s, 8s, 16s, up to 60s
    stop=stop_after_attempt(10) # Give Google 10 chances to respond
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
        model='gemini-2.5-flash', 
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
        You are a Pull Request Reviewer. 

        Your strict task is to identify Bugs, Security risks, and Code Hygiene issues ONLY in the newly modified code.
        
        Rules:
        1. ONLY critique lines that were added or modified in the GIT DIFF (lines starting with +).
        2. Do NOT report issues in existing legacy code that was not changed by this PR.
        3. Use the FULL PYTHON CODE purely as background context to understand how the new changes interact with the rest of the file.
        4. For every finding, provide a specific suggested_fix when a safe, actionable fix is possible.
        5. Return a valid JSON object with exactly three keys: bugs, security, and code_hygiene. Each key must contain an array of findings with description, location, severity, and suggested_fix.

        GIT DIFF (The actual changes to review):
        {git_diff}

        FULL PYTHON CODE (For background context only):
        {source_code}

        FLAKE8 REPORT (Syntactical errors):
        {lint_report}
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
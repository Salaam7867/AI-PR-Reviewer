import streamlit as st
import os
import difflib
import re
import concurrent.futures
from dotenv import load_dotenv
from github_service import fetch_pr_python_files
from graph import graph

load_dotenv()

st.set_page_config(page_title="AI PR Reviewer", layout="wide")

def get_pr_modified_lines(patch: str) -> set:
    """Calculates exactly which line numbers the original developer changed."""
    modified = set()
    if not patch or patch == "No diff available.":
        return modified
    current_line = 0
    for line in patch.splitlines():
        if line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            if match:
                current_line = int(match.group(1))
        elif line.startswith("+++") or line.startswith("---"):
            continue
        elif line.startswith("+"):
            modified.add(current_line)
            current_line += 1
        elif line.startswith(" "):
            current_line += 1
    return modified

def process_file_agent(file_data: dict) -> tuple:
    """Headless worker function that runs the LangGraph agent without UI blocking."""
    source_code = file_data['content']
    git_diff = file_data['patch'] or "No diff available." 
    
    pr_allowed_lines = get_pr_modified_lines(git_diff)
    
    initial_state = {
        "file_path": file_data['filename'],
        "original_source_code": source_code,
        "source_code": source_code,
        "git_diff": git_diff,  
        "lint_report": "",
        "ai_review": None,
        "fix_attempts": 0,
        "test_report": "",
        "final_status": "in_progress",
    }

    # Invoke the graph headlessly
    result = graph.invoke(initial_state)
    return file_data, result, pr_allowed_lines


st.title("🤖 Agentic AI PR Reviewer")
st.markdown("Paste a GitHub Pull Request URL to run the autonomous review and auto-fix loop concurrently.")

pr_url = st.text_input("GitHub PR URL", placeholder="https://github.com/username/repo/pull/1")

if st.button("Run PR Review"):
    github_token = os.getenv("GITHUB_TOKEN")
    if not github_token:
        st.error("Please add GITHUB_TOKEN to your .env file.")
        st.stop()
    if not pr_url:
        st.warning("Please enter a valid PR URL.")
        st.stop()

    with st.spinner("Fetching files from GitHub..."):
        try:
            changed_files = fetch_pr_python_files(pr_url, github_token)
        except Exception as e:
            st.error(f"Error fetching PR: {e}")
            st.stop()

    if not changed_files:
        st.info("No Python files were modified in this PR.")
        st.stop()

    # --- PARALLEL EXECUTION PHASE ---
    with st.status(f"Spawning {len(changed_files)} agents concurrently...", expanded=True) as status:
        st.write("Executing Linter & LLM Reviews in parallel...")
        
        # ThreadPoolExecutor launches a thread for every file simultaneously
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            completed_results = list(executor.map(process_file_agent, changed_files))
            
        status.update(label="All Concurrent Reviews Complete!", state="complete", expanded=False)

    # --- SEQUENTIAL UI RENDERING PHASE ---
    for file_data, result, pr_allowed_lines in completed_results:
        st.divider()
        st.subheader(f"Reviewing: `{file_data['filename']}`")
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("### 🔍 Agent Findings")
            if result.get("ai_review"):
                st.json(result["ai_review"].model_dump())
            else:
                st.write("No issues found.")
                
            st.markdown("### 🧹 Linter Logs")
            st.text(result.get("lint_report", "No linter output."))

        with col2:
            st.markdown("### 🛠️ Suggested Auto-Fix")
            
            # ---> STRICT LINE-BY-LINE GUARDRAIL <---
            original_lines = file_data['content'].splitlines()
            ai_lines = result["source_code"].splitlines()
            
            safe_final_lines = original_lines.copy()
            matcher = difflib.SequenceMatcher(None, original_lines, ai_lines)
            
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag != 'equal':
                    # Get the exact line numbers the AI tried to change
                    altered_lines = set(range(i1 + 1, i2 + 2))
                    
                    # ONLY allow the change if EVERY altered line is inside the PR map
                    if altered_lines.issubset(pr_allowed_lines):
                        safe_final_lines[i1:i2] = ai_lines[j1:j2]

            diff = list(difflib.unified_diff(original_lines, safe_final_lines, lineterm="", n=2))

            if diff:
                st.markdown("### 🚀 Guardrailed Code Changes (No Legacy Edits):")
                diff_text = "\n".join(diff)
                st.code(diff_text, language="diff")
            else:
                st.success("No in-scope code changes were necessary (ignored rogue formatting).")
import concurrent.futures
import streamlit as st
import os
import difflib
import re
from dotenv import load_dotenv
from github_service import fetch_pr_python_files, post_review_to_github
from graph import graph
# If you implemented the GitHub posting script earlier, import it here:
# from github_service import post_review_to_github

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
st.markdown("Paste a GitHub Pull Request URL to run the autonomous review and auto-fix loop.")

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

    # --- SEQUENTIAL EXECUTION PHASE (Rate-Limit Safe) ---
    completed_results = []
    
    with st.status(f"Processing {len(changed_files)} files sequentially...", expanded=True) as status:
        for file in changed_files:
            try:
                st.write(f"🕵️ Agent reviewing `{file['filename']}`...")
                result_tuple = process_file_agent(file)
                completed_results.append(result_tuple)
            except Exception as e:
                error_msg = str(e)
                if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg or "quota" in error_msg.lower():
                    st.error(f"⚠️ API Quota Exhausted on `{file['filename']}`. The AI reached its daily request limit. Halting further file reviews to preserve results.")
                    break # Stop processing, but keep what we have
                else:
                    st.error(f"❌ Error processing `{file['filename']}`: {error_msg}")
            
        status.update(label="Review Execution Complete!", state="complete", expanded=False)

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
                
# --- GITHUB POSTING PHASE ---
    if completed_results:
        st.divider()
        with st.spinner("Posting final report to GitHub..."):
            try:
                # Extract repo_name and pr_number from "https://github.com/owner/repo/pull/1"
                # Split by '/' and grab the relevant parts from the end of the URL
                url_parts = pr_url.rstrip('/').split('/')
                pr_number = int(url_parts[-1])
                repo_name = f"{url_parts[-4]}/{url_parts[-3]}"
                
                # Extract just the LangGraph state dictionaries from the completed_results tuple
                ai_results = [res[1] for res in completed_results]
                
                # Call your PyGithub script
                post_review_to_github(repo_name, pr_number, ai_results, github_token)
                st.success(f"✅ Successfully posted the agentic review to PR #{pr_number}!")
                
            except IndexError:
                st.error("❌ Could not parse the GitHub URL. Please ensure it looks like: https://github.com/owner/repo/pull/1")
            except Exception as e:
                st.error(f"❌ Failed to post review to GitHub: {e}")
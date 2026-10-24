import streamlit as st
import os
import difflib  # <-- Added here
from dotenv import load_dotenv
from github_service import fetch_pr_python_files
from graph import graph

load_dotenv()

st.set_page_config(page_title="AI PR Reviewer", layout="wide")

st.title("🤖 Agentic AI PR Reviewer")
st.markdown("Paste a GitHub Pull Request URL to run the autonomous review and auto-fix loop.")

# 1. User Input
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

    # 2. Process each file through your LangGraph
    for file_data in changed_files:
        st.subheader(f"Reviewing: `{file_data['filename']}`")
        
        source_code = file_data['content']
        git_diff = file_data['patch'] or "No diff available." 
        
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

        with st.status(f"Running Agent Loop on {file_data['filename']}...", expanded=True) as status:
            st.write("Executing Linter & LLM Review...")
            result = graph.invoke(initial_state)
            status.update(label="Review Complete!", state="complete", expanded=False)

        # 4. Display Results on the Streamlit UI
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
            
# ---> CLEANER DIFFLIB LOGIC <---
            original_lines = file_data['content'].splitlines()
            ai_fixed_lines = result["source_code"].splitlines()

            # Generate a standard unified diff with 2 lines of context
            diff = list(difflib.unified_diff(original_lines, ai_fixed_lines, lineterm="", n=2))

            if diff:
                st.markdown("### 🚀 Proposed Code Changes:")
                # Join the diff into a single string and display as standard git diff
                diff_text = "\n".join(diff)
                st.code(diff_text, language="diff")
            else:
                st.success("No code changes were necessary!")
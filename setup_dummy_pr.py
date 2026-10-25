import os
from github import Github, GithubException

# Ensure your token is set in your terminal: set GITHUB_TOKEN=your_token
token = os.getenv("GITHUB_TOKEN")
if not token:
    print("❌ Please set your GITHUB_TOKEN environment variable first.")
    exit(1)

g = Github(token)
user = g.get_user()

repo_name = "ai-reviewer-sandbox"

print(f"🤖 Agent starting: Creating repository '{repo_name}'...")
try:
    repo = user.create_repo(repo_name, description="Sandbox for testing AI PR Reviewer", private=False, auto_init=True)
except GithubException as e:
    if e.status == 422: # Repo already exists
        repo = user.get_repo(repo_name)
        print(f"⚠️ Repository already exists. Using existing repo.")
    else:
        raise e

# The incredibly broken code
bad_code = """import os
import sys

def process_user_input(user_data, cache=[]):
    # SECURITY ISSUE: eval() is highly dangerous
    result = eval(user_data)
    
    # BUG: mutable default argument 'cache' will cause memory leaks
    cache.append(result)
    
    # HYGIENE: unused import (sys), bad spacing
    print( "Done" )
"""

branch_name = "test-bad-code"
print(f"🌿 Creating branch '{branch_name}' and injecting broken code...")

try:
    # Get the main branch commit to branch off from
    main_ref = repo.get_git_ref("heads/main")
    repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=main_ref.object.sha)
    
    # Create the file with the bad code
    repo.create_file("calculator.py", "Add calculator with deliberate bugs", bad_code, branch=branch_name)
except GithubException as e:
    print(f"⚠️ Branch or file might already exist. Proceeding to PR creation...")

print("🚀 Opening Pull Request...")
try:
    pr = repo.create_pull(
        title="feat: add user input processing",
        body="Adding a new calculator function. Please review.",
        head=branch_name,
        base="main"
    )
    print("\n✅ DONE! Paste this exact URL into your Streamlit App:")
    print(f"👉 {pr.html_url}")
except GithubException as e:
    if e.status == 422:
         print("\n⚠️ A Pull Request for this branch already exists. Check your GitHub repository!")
    else:
         print(f"❌ Failed to create PR: {e}")
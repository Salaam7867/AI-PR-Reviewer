from github import Github
import re

def parse_pr_url(url: str):
    """Extracts the repo name and PR number from a GitHub URL."""
    # Example URL: https://github.com/Salaam7867/test-repo/pull/1
    match = re.search(r"github\.com/([^/]+/[^/]+)/pull/(\d+)", url)
    if not match:
        raise ValueError("Invalid GitHub PR URL format.")
    return match.group(1), int(match.group(2))

def fetch_pr_python_files(pr_url: str, github_token: str):
    """Fetches the raw source code of Python files modified in the PR."""
    repo_name, pr_number = parse_pr_url(pr_url)
    
    g = Github(github_token)
    repo = g.get_repo(repo_name)
    pr = repo.get_pull(pr_number)
    
    python_files = []
    # Loop through the files changed in this PR
    for file in pr.get_files():
        if file.filename.endswith(".py"):
            # Get the actual complete code of the file after the changes
            file_content = repo.get_contents(file.filename, ref=pr.head.sha).decoded_content.decode("utf-8")
            python_files.append({
                "filename": file.filename,
                "content": file_content,
                "patch": file.patch  # The exact diff (lines added/removed)
            })
            
    return python_files
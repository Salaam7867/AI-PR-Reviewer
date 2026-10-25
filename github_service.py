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


def post_review_to_github(repo_name: str, pr_number: int, ai_results: list, github_token: str):
    """Formats the AI reviews and posts them as a single comment on the GitHub PR."""
    g = Github(github_token)
    repo = g.get_repo(repo_name)
    pr = repo.get_pull(pr_number)
    
    comment_body = "## 🤖 Agentic AI PR Review\n\n"
    issues_found = False
    
    for result in ai_results:
        filename = result.get("file_path", "Unknown File")
        ai_review = result.get("ai_review")
        
        # Check if the Pydantic model actually caught any issues
        if ai_review and (ai_review.bugs or ai_review.security or ai_review.code_hygiene):
            issues_found = True
            comment_body += f"### 📄 `{filename}`\n"
            
            categories = [
                ("🐛 Bugs", ai_review.bugs), 
                ("🔒 Security", ai_review.security), 
                ("🧹 Code Hygiene", ai_review.code_hygiene)
            ]
            
            for category_name, findings in categories:
                if findings:
                    comment_body += f"**{category_name}**\n"
                    for finding in findings:
                        comment_body += f"- **[{finding.severity}]** {finding.description} (Location: `{finding.location}`)\n"
                        if getattr(finding, "suggested_fix", None):
                            comment_body += f"  - *Suggested Fix:* `{finding.suggested_fix}`\n"
            comment_body += "\n---\n"
            
    if not issues_found:
        comment_body += "✅ All files passed the AI review! No critical issues found."
        
    # Post the markdown as a PR comment
    pr.create_issue_comment(comment_body)
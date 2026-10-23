
from typing import TypedDict

from review_schema import CodeReview


class ReviewState(TypedDict):
    file_path: str
    original_source_code: str
    source_code: str
    git_diff: str     # <-- ADD THIS LINE
    lint_report: str
    ai_review: CodeReview | None
    fix_attempts: int
    test_report: str
    final_status: str



from typing import TypedDict

from review_schema import CodeReview


class ReviewState(TypedDict):
    file_path: str
    source_code: str
    lint_report: str
    ai_review: CodeReview
    verification_report: dict

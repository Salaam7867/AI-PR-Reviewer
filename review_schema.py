from typing import Literal
from pydantic import BaseModel


class ReviewFinding(BaseModel):
    description: str
    location: str
    severity: Literal["low", "medium", "high", "potential"]


class CodeReview(BaseModel):
    bugs: list[ReviewFinding]
    security: list[ReviewFinding]
    code_hygiene: list[ReviewFinding]
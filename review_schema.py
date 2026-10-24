from typing import Literal
from pydantic import BaseModel, field_validator

class ReviewFinding(BaseModel):
    description: str
    location: str
    severity: Literal["low", "medium", "high", "potential"]
    suggested_fix: str | None = None

    # This intercepts the AI's output and forces it to lowercase before validation
    @field_validator('severity', mode='before')
    @classmethod
    def lowercase_severity(cls, v):
        if isinstance(v, str):
            return v.lower()
        return v


class CodeReview(BaseModel):
    bugs: list[ReviewFinding]
    security: list[ReviewFinding]
    code_hygiene: list[ReviewFinding]
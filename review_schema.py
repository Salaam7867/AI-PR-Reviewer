from typing import Literal, List
from pydantic import BaseModel, Field, field_validator

class ReviewFinding(BaseModel):
    description: str
    location: str
    severity: Literal["low", "medium", "high", "potential"]
    suggested_fix: str | None = None

    # This intercepts the AI's output, fixes casing, and maps synonyms
    @field_validator('severity', mode='before')
    @classmethod
    def normalize_severity(cls, v):
        if isinstance(v, str):
            val = v.lower().strip()
            
            # Map common AI hallucinations to our strict schema
            synonyms = {
                'minor': 'low',
                'info': 'low',
                'warning': 'medium',
                'critical': 'high',
                'severe': 'high'
            }
            if val in synonyms:
                return synonyms[val]
                
            # If it invents a totally random word, default to 'potential' to prevent a crash
            if val not in ['low', 'medium', 'high', 'potential']:
                return 'potential'
                
            return val
        return v


class CodeReview(BaseModel):
    bugs: List[ReviewFinding] = Field(default_factory=list)
    security: List[ReviewFinding] = Field(default_factory=list)
    code_hygiene: List[ReviewFinding] = Field(default_factory=list)
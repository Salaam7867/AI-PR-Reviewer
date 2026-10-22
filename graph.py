
from langgraph.graph import StateGraph, START, END
from pathlib import Path
from graph_state import ReviewState
from graph_nodes import linter_node, reviewer_node,fixer_node



MAX_FIX_ATTEMPTS = 2



def route_after_review(state: ReviewState) -> str:
    review = state["ai_review"]

    if review is None:
        return "end"

    findings = (
        review.bugs
        + review.security
        + review.code_hygiene
    )

    has_actionable_findings = any(
        finding.suggested_fix
        for finding in findings
    )

    if not has_actionable_findings:
        return "end"

    if state["fix_attempts"] >= MAX_FIX_ATTEMPTS:
        return "end"

    return "fixer"



def route_after_fix(state: ReviewState) -> str:
    """Re-review the final code, even after the last allowed fix."""
    return "linter"


def finalize_review(state: ReviewState) -> dict:
    review = state["ai_review"]

    if review is None:
        return {"final_status": "review_failed"}

    has_findings = bool(
        review.bugs or review.security or review.code_hygiene
    )

    if state["lint_report"] == "No linting issues found." and not has_findings:
        return {"final_status": "completed"}

    return {"final_status": "completed_with_findings"}


builder = StateGraph(ReviewState)

builder.add_node("linter", linter_node)
builder.add_node("reviewer", reviewer_node)
builder.add_node("fixer", fixer_node)
builder.add_node("finalize", finalize_review)

builder.add_edge(START, "linter")
builder.add_edge("linter", "reviewer")

builder.add_conditional_edges(
    "reviewer",
    route_after_review,
    {
        "fixer": "fixer",
        "end": "finalize",
    },
)

builder.add_conditional_edges(
    "fixer",
    route_after_fix,
    {
        "linter": "linter",
        "end": "finalize",
    },
)

builder.add_edge("finalize", END)

graph = builder.compile()


if __name__ == "__main__":
    source_code = Path("sample.py").read_text(encoding="utf-8")

    initial_state = {
        "file_path": "sample.py",
        "original_source_code": source_code,
        "source_code": source_code,
        "lint_report": "",
        "ai_review": None,
        "fix_attempts": 0,
        "test_report": "",
        "final_status": "in_progress",
    }

    result = graph.invoke(initial_state)

    print("FINAL GRAPH STATE:")
    print(result)

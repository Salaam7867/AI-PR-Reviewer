
from graph_state import ReviewState
from linter_tool import run_linter


def linter_node(state: ReviewState) -> dict:
    file_path = state["file_path"]
    lint_report = run_linter(file_path)

    return {"lint_report": lint_report}


if __name__ == "__main__":
    test_state = {
        "file_path": "sample.py",
        "source_code": "",
        "lint_report": "",
        "ai_review": None,
        "verification_report": {},
    }

    result = linter_node(test_state)

    print("LINTER NODE OUTPUT:")
    print(result)

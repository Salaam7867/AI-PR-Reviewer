
from langgraph.graph import StateGraph, START, END

from graph_state import ReviewState
from graph_nodes import linter_node, reviewer_node



builder = StateGraph(ReviewState)

builder.add_node("linter", linter_node)
builder.add_node("reviewer", reviewer_node)

builder.add_edge(START, "linter")
builder.add_edge("linter", "reviewer")
builder.add_edge("reviewer", END)

graph = builder.compile()


if __name__ == "__main__":
    initial_state = {
        "file_path": "sample.py",
        "source_code": "",
        "lint_report": "",
        "ai_review": None,
        "verification_report": {},
    }

    result = graph.invoke(initial_state)

    print("FINAL GRAPH STATE:")
    print(result)

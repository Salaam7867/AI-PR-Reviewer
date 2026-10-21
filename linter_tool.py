import subprocess
import sys


def run_linter(file_path: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "flake8", file_path],
        capture_output=True,
        text=True
    )

    report = result.stdout + result.stderr

    if result.returncode == 0:
        return "No linting issues found."

    return report or f"Flake8 exited with code {result.returncode}."
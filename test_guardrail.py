import os
import unittest

# Allow importing graph_nodes.py without a real API key.
# These tests do not make Groq API calls.
os.environ.setdefault("GROQ_API_KEY", "test-key")

from graph_nodes import is_fix_in_scope


class TestFixScope(unittest.TestCase):

    def setUp(self):
        self.original_code = """def hello():
    print("Hello")

def calculate(a, b):
    return a / b

def goodbye():
    print("Goodbye")"""

    def test_legitimate_multiline_fix(self):
        fixed_code = self.original_code.replace(
            "    return a / b",
            "    if b == 0:\n"
            "        return 0\n"
            "    return a / b"
        )

        self.assertTrue(
            is_fix_in_scope(
                self.original_code,
                fixed_code,
                {5}
            )
        )

    def test_reject_unrelated_insertion_at_eof(self):
        fixed_code = (
            self.original_code
            + "\n\nprint('Unrelated change')"
        )

        self.assertFalse(
            is_fix_in_scope(
                self.original_code,
                fixed_code,
                {5}
            )
        )

    def test_reject_unrelated_modification(self):
        fixed_code = self.original_code.replace(
            'print("Goodbye")',
            'print("Modified unrelated code")'
        )

        self.assertFalse(
            is_fix_in_scope(
                self.original_code,
                fixed_code,
                {5}
            )
        )

    def test_reject_when_no_changed_lines_exist(self):
        self.assertFalse(
            is_fix_in_scope(
                self.original_code,
                self.original_code,
                set()
            )
        )


    
    def test_normal_one_line_repair_is_accepted(self):
        original = (
            "def calculate(value):\n"
            "    result = value + 1\n"
            "    return result\n"
        )
        fixed = (
            "def calculate(value):\n"
            "    result = value + 2\n"
            "    return result\n"
        )

        self.assertTrue(is_fix_in_scope(original, fixed, {2}))

    def test_legitimate_multiline_replacement_is_accepted(self):
        original = (
            "def calculate(value):\n"
            "    result = value + 1\n"
            "    return result\n"
        )
        fixed = (
            "def calculate(value):\n"
            "    if value is None:\n"
            "        raise ValueError('value is required')\n"
            "    result = value + 1\n"
            "    return result\n"
        )

        self.assertTrue(is_fix_in_scope(original, fixed, {2}))

    def test_unrelated_adjacent_insertion_is_rejected(self):
        original = (
            "def calculate(value):\n"
            "    result = value + 1\n"
            "    return result\n"
        )
        fixed = (
            "def calculate(value):\n"
            "    result = value + 1\n"
            "    print('debug')\n"
            "    return result\n"
        )

        self.assertFalse(is_fix_in_scope(original, fixed, {2}))

    def test_distant_unrelated_modification_is_rejected(self):
        original = (
            "def calculate(value):\n"
            "    result = value + 1\n"
            "    return result\n"
            "\n"
            "def unrelated():\n"
            "    return True\n"
        )
        fixed = (
            "def calculate(value):\n"
            "    result = value + 1\n"
            "    return result\n"
            "\n"
            "def unrelated():\n"
            "    return False\n"
        )

        self.assertFalse(is_fix_in_scope(original, fixed, {2}))



if __name__ == "__main__":
    unittest.main(verbosity=2)



import difflib

def is_fix_in_scope(original_code: str, fixed_code: str, valid_lines: set[int]) -> bool:
    if not valid_lines:
        return False 
        
    matcher = difflib.SequenceMatcher(None, original_code.splitlines(), fixed_code.splitlines())
    
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != 'equal':
            if tag == 'insert':
                insertion_point = i1 + 1
                if not any(abs(insertion_point - v) <= 1 for v in valid_lines):
                    print(f"🛡️ Guardrail Blocked: 'insert' operation at line {insertion_point} is out of scope.")
                    return False
            else:
                changed_lines = set(range(i1 + 1, i2 + 1))
                if not changed_lines.issubset(valid_lines):
                    out_of_bounds = changed_lines - valid_lines
                    print(f"🛡️ Guardrail Blocked: '{tag}' operation modified out-of-scope lines {out_of_bounds}.")
                    return False
    return True

# Simplified Mock Data (8 lines total)
original_source = """def hello():
    print("Hello")

def calculate(a, b):
    return a / b

def goodbye():
    print("Goodbye")"""

# The PR diff says only line 5 was changed (return a / b)
valid_pr_lines = {5} 

print("--- TEST 3: Legitimate Multi-Line Fix (False Negative Check) ---")
# A real fix requires adding a zero-division check before the return statement.
# This modifies line 5, but effectively inserts/replaces code that pushes into new lines.
valid_multiline_fix = original_source.replace(
    "    return a / b", 
    "    if b == 0:\n        return 0\n    return a / b"
)
result = is_fix_in_scope(original_source, valid_multiline_fix, valid_pr_lines)
print(f"Result: {'✅ Accepted' if result else '❌ Rejected'}\n")

print("--- TEST 4: Unrelated Insertion at EOF (False Positive Check) ---")
# Appending code to the very end of the file (Line 9)
eof_insertion = original_source + "\n\nprint('EOF')"
result = is_fix_in_scope(original_source, eof_insertion, valid_pr_lines)
print(f"Result: {'✅ Accepted' if result else '❌ Rejected'}\n")
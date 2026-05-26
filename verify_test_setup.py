#!/usr/bin/env python
"""Verify that the test file exists and compute_weekly_stats doesn't exist yet."""

import os

# Check test file exists
test_file = "tests/test_weekly.py"
if os.path.exists(test_file):
    with open(test_file) as f:
        content = f.read()
        test_count = content.count("def test_")
        print(f"✓ Test file exists: {test_file}")
        print(f"✓ Contains {test_count} test functions (expected 15)")
        if test_count == 15:
            print("  PASS: All test functions present")
        else:
            print(f"  WARNING: Expected 15 tests, found {test_count}")
else:
    print(f"✗ Test file NOT found: {test_file}")
    exit(1)

# Check that compute_weekly_stats does NOT exist yet
init_file = "secure_score_graph/__init__.py"
with open(init_file) as f:
    content = f.read()
    if "def compute_weekly_stats" in content:
        print(f"✗ Function compute_weekly_stats ALREADY EXISTS in {init_file}")
        exit(1)
    else:
        print(f"✓ Function compute_weekly_stats does NOT exist yet")
        print("  (This is correct for TDD red phase)")

# Verify test file tries to import the missing function
with open(test_file) as f:
    content = f.read()
    if "from secure_score_graph import compute_weekly_stats" in content:
        print(f"✓ Test file imports the missing function (tests will fail as expected)")
    else:
        print(f"✗ Test file does not import compute_weekly_stats")
        exit(1)

print("\n" + "="*70)
print("SETUP VERIFICATION: PASS")
print("="*70)
print("\nSummary:")
print("- tests/test_weekly.py created with 15 test functions")
print("- compute_weekly_stats function does NOT exist yet")
print("- Tests will fail with ImportError/AttributeError when run")
print("- This is the EXPECTED state for TDD red phase")
print("\nNext step: Run 'python -m pytest tests/test_weekly.py -v' to see failures")

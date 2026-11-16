---
name: code-review
description: Review a diff for bugs, missing tests, and boundary cases.
---

# Code review

When this skill applies:

1. Read the changed files and the relevant tests.
2. Look for correctness bugs, unhandled boundaries, and missing regression tests.
3. Run the narrowest useful test command and report its exit code.
4. Separate confirmed findings from suggestions; do not invent behaviour.
5. Finish with a short list of required fixes and optional improvements.

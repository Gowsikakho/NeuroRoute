---
name: Backend Python test imports
description: Run the nested NeuroRoute Python tests in this Replit workspace.
---

Run the NeuroRoute backend tests from `artifacts/neuroroute-sdn/backend` with `PYTHONPATH=.`. This environment does not have pytest installed; the tests use `unittest`, so invoke `PYTHONPATH=. uv run --project ../../.. python -m unittest discover -s tests -p 'test_*.py'` after changing into the backend directory.

**Why:** The documented pytest command fails because pytest is absent, and running discovery from the workspace root does not find the nested test directory.

**How to apply:** Run the unittest command from the backend directory so the nested package is on Python's import path.

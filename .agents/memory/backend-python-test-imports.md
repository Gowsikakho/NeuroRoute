---
name: Backend Python test imports
description: Run pytest for the nested NeuroRoute Python package in this Replit workspace.
---

Run the NeuroRoute backend tests from `artifacts/neuroroute-sdn/backend` with `PYTHONPATH=.`. In this environment, invoking `uv run --directory artifacts/neuroroute-sdn/backend pytest` from the workspace root still fails to import `neuroroute_api`.

**Why:** Pytest ran with the workspace root on the import path rather than the nested backend package.

**How to apply:** Follow the backend test command in `artifacts/neuroroute-sdn/README.md`.

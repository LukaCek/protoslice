# ProtoSlice v2 — baseline tests (step 1)

These tests **do not change v0.1.x code or run the real OrcaSlicer binary**. They pin legacy response shapes and validate a reproducible 20 mm cube fixture.

Run at repository root:

```bash
python -m pip install -r requirements.txt pytest
python -m pytest -q tests/baseline/
```

The fixture geometry test uses only Python's standard library. Legacy API tests require Flask and requests.

These are **baseline tests, not proof of slicing correctness**. The next step adds real OrcaSlicer integration tests and verified expected statistics.

Never compare a new v2 mass-in-grams response against legacy `used_cm3` as though they represented the same unit.

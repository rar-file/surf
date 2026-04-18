# Contributing to SURF

Thanks for contributing.

## Ground rules

- Keep changes focused and small.
- Prefer normal commit history over large dump commits.
- Add or update tests when changing deterministic logic.
- Keep runtime state and secrets out of git.

## Local setup

```bash
python -m venv venv
source venv/bin/activate  # Windows: .\\venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
pip install pytest
```

## Running tests

```bash
pytest
```

## What to contribute

Good first improvements:
- bug fixes
- test coverage
- docs clarity
- provider reliability
- search / memory UX improvements

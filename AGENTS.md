# Project conventions

## Testing

Every new feature or bug fix must include a test.
Python backend tests run with `uv run pytest`.

## Commands

- `uv run pytest` — Python tests (app, db, AI, UI rendering)
- `npm test` — browser UI tests (jsdom, tests/ui/ui.test.mjs)
- `ruff` — lint via `uv run ruff check`
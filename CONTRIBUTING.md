# Contributing to remarkable-morning

Thanks for your interest in contributing! remarkable-morning is a daily news
digest pipeline for the reMarkable Paper Pro, and contributions of all sizes
are welcome — bug fixes, new features, documentation, and source feeds.

This guide covers how to contribute code, request features, report bugs, and
set up a development environment. A quick table of contents:

- [Code contributions](#code-contributions)
- [Feature requests](#feature-requests)
- [Bug reports](#bug-reports)
- [Development setup](#development-setup)
- [Code style](#code-style)
- [Commit conventions](#commit-conventions)

---

## Code contributions

Contributions are made through pull requests. The basic flow:

1. **Fork** the repository on GitHub and clone your fork locally.
2. **Create a branch** from `main` with a descriptive name. Branches follow
   the conventional-commit type as a prefix:

   ```bash
   git checkout -b feat/add-guardian-section
   # or: fix/..., docs/..., refactor/..., test/..., ci/..., chore/...
   ```

3. **Make your changes** — see [Code style](#code-style) for the conventions
   this project follows.
4. **Add or update tests** for your change. New features and bug fixes require
   tests; see [Testing](#testing) below.
5. **Run the full test suite** locally and make sure it passes:

   ```bash
   pip install -e ".[dev]"
   pytest
   ```

   All tests use mocks — no network calls or `rmapi` required — so they run
   anywhere with Python 3.11+.
6. **Commit** your changes following the [commit conventions](#commit-conventions).
   Keep commits focused; one logical change per commit.
7. **Push** your branch and **open a pull request** against `main`.

   ```bash
   git push -u origin HEAD
   ```

   In the PR description, describe **what** changed and **why**, and note any
   manual testing you did. Link the issue your PR addresses (e.g.
   `Closes #8`) so it closes automatically on merge.

8. **Wait for review.** A maintainer will review your PR. Be responsive to
   feedback — small changes are expected. CI must be green before merge.

### Pull request checklist

- [ ] Branch is named with a conventional-commit prefix
- [ ] Tests pass locally (`pytest`)
- [ ] New features have tests; bug fixes have a regression test
- [ ] Commit message follows [conventional commits](#commit-conventions)
- [ ] No secrets, API keys, or credentials committed
- [ ] `feeds.yaml` is **not** committed (use `feeds.example.yaml` for examples)

---

## Feature requests

Have an idea for a new source, category, curation option, or integration?

1. Search [existing issues](https://github.com/vevmesteren/remarkable-morning/issues)
   to avoid duplicates.
2. Open a new issue and apply the **`enhancement`** label.
3. Describe the feature and the use case it solves. If it involves config,
   sketch the `feeds.yaml` shape you'd expect.

Feature requests are triaged by maintainers. Clear motivation and a concrete
proposal move fastest.

---

## Bug reports

Found something broken?

1. Search [existing issues](https://github.com/vevmesteren/remarkable-morning/issues)
   first — the bug may already be reported.
2. Open a new issue and apply the **`bug`** label.
3. Include **reproduction steps** so a maintainer can recreate the problem:

   - **Environment:** Python version, OS, and how you're running the
     pipeline (`python -m remarkable_morning.pipeline`, the systemd timer, etc.)
   - **Steps to reproduce:** the exact commands you ran and any config
     (`feeds.yaml`) relevant to the bug — redact API keys.
   - **Expected behavior:** what you thought would happen.
   - **Actual behavior:** what happened instead, including the full traceback
     or log output.
   - **Pipeline stage:** which stage failed (Fetch, Extract, Curate, EPUB
     Assembly, Upload, Cleanup) if applicable.

> **Never paste API keys, `.env.pipeline` contents, or `rmapi` tokens into a
> bug report.** They are secrets. Redact them before sharing logs or config.

---

## Development setup

remarkable-morning is a Python 3.11+ project. To get a working local copy:

1. **Install Python 3.11+** and clone the repository.
2. **Set up the full pipeline** — see the
   [Installation](README.md#installation) section of the README, or run the
   interactive **setup wizard** which walks you through each step (each step
   is skippable):

   ```bash
   python -m remarkable_morning.setup
   ```

3. **Install dev dependencies** (pulls in `pytest`):

   ```bash
   pip install -e ".[dev]"
   ```

You don't need a reMarkable, `rmapi`, live API keys, or network access to
develop and test — the test suite is fully mocked. See
[Testing](#testing) below.

### Project layout

```
src/remarkable_morning/     # pipeline source — do not modify without discussion
tests/                       # pytest test suite (fully mocked)
feeds.example.yaml           # sanitized example config
systemd/                      # systemd --user unit files
```

> The core pipeline modules (`config.py`, `sources/`, `extract.py`,
> `curate.py`, `epub.py`, `upload.py`, `cleanup.py`, `pipeline.py`) are
> stable. If your change touches them, open an issue to discuss the approach
> **before** opening a PR.

---

## Code style

remarkable-morning follows standard Python conventions:

- **PEP 8** — keep lines readable; existing code uses 4-space indentation and
  trailing commas in multi-line collections.
- **Type hints** — public functions use type annotations (`->` return types
  and parameter types). Match the existing style when adding or modifying
  code.
- **Docstrings** — public functions and modules carry short docstrings
  explaining intent.
- **No secrets in code or config** — API keys are read from environment
  variables; `feeds.example.yaml` is the only config file that belongs in the
  repo. Never hardcode a key.

### Testing

The test suite is in `tests/` and runs with `pytest`:

```bash
# Install dev dependencies (once)
pip install -e ".[dev]"

# Run all tests
pytest

# Verbose output
pytest -v

# A single test file
pytest tests/test_curate.py
```

All tests use mocks — no network calls, no `rmapi`, no live API keys. They
run on any machine with Python 3.11+. **New features and bug fixes must
include tests.** A regression test for a bug fix is the proof the bug is gone.

When adding tests, follow the existing patterns in `tests/`: mock external
calls (`httpx`, `rmapi` subprocess), use the example feeds config for
fixtures, and keep tests independent of each other.

---

## Commit conventions

This project uses [Conventional Commits](https://www.conventionalcommits.org/).
Format your commit messages as:

```
type(optional-scope): short description in imperative mood

Optional body explaining the why, wrapped at 72 characters.
```

### Types

| Type | Use for |
|------|---------|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `refactor` | Code restructuring without behavior change |
| `test` | Adding or correcting tests |
| `ci` | CI/build/pipeline changes |
| `chore` | Maintenance, deps, tooling |
| `perf` | Performance improvement |

### Examples

```
feat(guardian): add technology section to default sources
fix(curate): handle empty article list without crashing
docs: add CONTRIBUTING.md
test: cover cross-source deduplication edge case
ci: run pytest on Python 3.11 and 3.12
```

Keep the subject line under 72 characters, imperative mood
("add" not "added"), and no trailing period. Use the body to explain
*why* the change is needed — the *what* is already in the diff.

---

## Questions

If something in this guide is unclear or you're not sure where to start,
open an issue with the **`question`** label, or pick a
**`good first issue`** / **`help wanted`** item from the issue tracker.

Thanks for contributing — and enjoy your morning paper. ☕
"""Interactive CLI setup wizard for Remarkable Morning.

Run with::

    python -m remarkable_morning.setup

The wizard guides a new user through every setup step.  Each step is
skippable — press Enter at any prompt to skip.  A summary is printed at
the end.

Design contracts
----------------
  - MF-2: This module does NOT import any pipeline internals (config,
    extract, curate, epub, upload, cleanup, pipeline, sources/).  It
    orchestrates external tools directly.
  - MF-3: All external calls (httpx, subprocess, input/getpass) are routed
    through module-level wrapper functions so tests can monkeypatch them
    without touching the real network or filesystem.
"""

from __future__ import annotations

import getpass
import os
import platform
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import httpx
except ImportError:  # pragma: no cover — httpx is a core dependency
    httpx = None


# ---------------------------------------------------------------------------
# ANSI colours (no external dependency)
# ---------------------------------------------------------------------------

_BOLD = "\033[1m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_DIM = "\033[2m"
_RESET = "\033[0m"

_IS_TTY = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    """Wrap *text* in an ANSI colour code (no-op when not a TTY)."""
    if not _IS_TTY:
        return text
    return f"{code}{text}{_RESET}"


# ---------------------------------------------------------------------------
# Mockable external-call wrappers (MF-3)
# ---------------------------------------------------------------------------

def _run_command(cmd: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    """Run *cmd* via subprocess and return the ``CompletedProcess``.

    Tests monkeypatch this to avoid real subprocess calls.
    """
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
    )


def _http_get(
    url: str, headers: dict | None = None, timeout: int = 10
) -> tuple[int, str]:
    """HTTP GET *url* and return ``(status_code, body)``.

    Tests monkeypatch this to avoid real network calls.
    """
    if httpx is None:  # pragma: no cover
        raise RuntimeError("httpx is required for API key validation")
    resp = httpx.get(url, headers=headers or {}, timeout=timeout)
    return resp.status_code, resp.text


def _prompt(message: str) -> str:
    """Read a line of user input.  Tests monkeypatch this."""
    return input(message)


def _prompt_secret(message: str) -> str:
    """Read masked user input (for API keys).  Tests monkeypatch this."""
    return getpass.getpass(message)


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

@dataclass
class SetupResult:
    """Outcome of a single wizard step."""

    name: str
    status: str  # "done" | "skipped" | "failed" | "warning"
    detail: str = ""


@dataclass
class WizardState:
    """Accumulated state across wizard steps."""

    results: list[SetupResult] = field(default_factory=list)
    guardian_key: str = ""
    openrouter_key: str = ""

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.results.append(SetupResult(name, status, detail))


# ---------------------------------------------------------------------------
# Console helpers
# ---------------------------------------------------------------------------

def _print_header(title: str) -> None:
    print(f"\n{_c(_BOLD, f'=== {title} ===')}")


def _ok(msg: str) -> None:
    print(f"  {_c(_GREEN, '✓')} {msg}")


def _fail(msg: str) -> None:
    print(f"  {_c(_RED, '✗')} {msg}")


def _warn(msg: str) -> None:
    print(f"  {_c(_YELLOW, '⚠')} {msg}")


def _info(msg: str) -> None:
    print(f"  {_c(_DIM, msg)}")


def _ask_yes_no(prompt: str, default: bool = True) -> bool:
    """Ask a yes/no question.  Empty input returns *default*."""
    suffix = " [Y/n] " if default else " [y/N] "
    raw = _prompt(f"{prompt}{suffix}").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


# ---------------------------------------------------------------------------
# Step 1 — Python version
# ---------------------------------------------------------------------------

def _python_version() -> tuple[int, int]:
    """Return ``(major, minor)`` Python version.  Tests monkeypatch this."""
    return sys.version_info[:2]


def check_python() -> SetupResult:
    """Check the running Python version is 3.11+."""
    _print_header("Step 1: Python version")
    major, minor = _python_version()
    if major > 3 or (major == 3 and minor >= 11):
        _ok(f"Python {major}.{minor} — OK")
        return SetupResult("Python version", "done", f"{major}.{minor}")
    _fail(f"Python {major}.{minor} — requires 3.11+")
    return SetupResult("Python version", "failed", f"{major}.{minor}")


# ---------------------------------------------------------------------------
# Step 2 — rmapi installed?
# ---------------------------------------------------------------------------

def check_rmapi() -> SetupResult:
    """Check that rmapi is installed and callable."""
    _print_header("Step 2: rmapi")
    if not _ask_yes_no("Check rmapi installation?", default=True):
        _info("Skipped")
        return SetupResult("rmapi check", "skipped")
    try:
        result = _run_command(["rmapi", "--version"])
        if result.returncode == 0:
            version = (result.stdout or result.stderr).strip()
            _ok(f"rmapi found — {version}")
            return SetupResult("rmapi check", "done", version)
        _fail(f"rmapi returned error (exit {result.returncode})")
        return SetupResult("rmapi check", "failed", f"exit {result.returncode}")
    except FileNotFoundError:
        _fail("rmapi not found — install from https://github.com/ddvk/rmapi/releases")
        return SetupResult("rmapi check", "failed", "not installed")
    except subprocess.TimeoutExpired:
        _fail("rmapi check timed out")
        return SetupResult("rmapi check", "failed", "timeout")
    except Exception as exc:  # noqa: BLE001
        _fail(f"rmapi check error: {exc}")
        return SetupResult("rmapi check", "failed", str(exc))


# ---------------------------------------------------------------------------
# Step 3 — Guardian API key
# ---------------------------------------------------------------------------

GUARDIAN_TEST_URL = "https://content.guardianapis.com/search"


def test_guardian_key(key: str) -> bool:
    """Validate a Guardian Content API key by making a test request."""
    try:
        status, _body = _http_get(GUARDIAN_TEST_URL, headers={"api-key": key})
        return status == 200
    except Exception:  # noqa: BLE001 — network errors mean invalid
        return False


def prompt_guardian_key() -> tuple[SetupResult, str]:
    """Prompt for and validate the Guardian API key.

    Returns ``(result, key)`` — *key* is the entered value (empty if
    skipped).
    """
    _print_header("Step 3: Guardian API key")
    if not _ask_yes_no("Configure Guardian API key?", default=True):
        _info("Skipped")
        return SetupResult("Guardian key", "skipped"), ""
    key = _prompt_secret("  Guardian API key (paste, input hidden): ").strip()
    if not key:
        _info("No key entered — skipped")
        return SetupResult("Guardian key", "skipped"), ""
    _info("Testing key…")
    if test_guardian_key(key):
        _ok("Guardian API key is valid")
        return SetupResult("Guardian key", "done", "validated"), key
    _warn("Key validation failed — saved anyway, you can fix later")
    return SetupResult("Guardian key", "warning", "validation failed"), key


# ---------------------------------------------------------------------------
# Step 4 — OpenRouter API key
# ---------------------------------------------------------------------------

OPENROUTER_TEST_URL = "https://openrouter.ai/api/v1/key"


def test_openrouter_key(key: str) -> bool:
    """Validate an OpenRouter API key."""
    try:
        status, _body = _http_get(
            OPENROUTER_TEST_URL, headers={"Authorization": f"Bearer {key}"}
        )
        return status == 200
    except Exception:  # noqa: BLE001
        return False


def prompt_openrouter_key() -> tuple[SetupResult, str]:
    """Prompt for and validate the OpenRouter API key.

    Returns ``(result, key)`` — *key* is the entered value (empty if
    skipped).
    """
    _print_header("Step 4: OpenRouter API key")
    if not _ask_yes_no("Configure OpenRouter API key?", default=True):
        _info("Skipped")
        return SetupResult("OpenRouter key", "skipped"), ""
    key = _prompt_secret("  OpenRouter API key (paste, input hidden): ").strip()
    if not key:
        _info("No key entered — skipped")
        return SetupResult("OpenRouter key", "skipped"), ""
    _info("Testing key…")
    if test_openrouter_key(key):
        _ok("OpenRouter API key is valid")
        return SetupResult("OpenRouter key", "done", "validated"), key
    _warn("Key validation failed — saved anyway, you can fix later")
    return SetupResult("OpenRouter key", "warning", "validation failed"), key


# ---------------------------------------------------------------------------
# Step 5 — Copy feeds.example.yaml → feeds.yaml
# ---------------------------------------------------------------------------

def copy_feeds_config(project_dir: Path) -> SetupResult:
    """Copy ``feeds.example.yaml`` to ``feeds.yaml`` (with confirmation)."""
    _print_header("Step 5: feeds.yaml")
    if not _ask_yes_no("Copy feeds.example.yaml to feeds.yaml?", default=True):
        _info("Skipped")
        return SetupResult("feeds.yaml", "skipped")
    source = project_dir / "feeds.example.yaml"
    target = project_dir / "feeds.yaml"
    if target.exists():
        if not _ask_yes_no("feeds.yaml already exists — overwrite?", default=False):
            _info("Kept existing feeds.yaml")
            return SetupResult("feeds.yaml", "skipped", "already exists")
    if not source.exists():
        _fail(f"feeds.example.yaml not found at {source}")
        return SetupResult("feeds.yaml", "failed", "source missing")
    shutil.copy2(source, target)
    _ok("Copied feeds.example.yaml → feeds.yaml")
    return SetupResult("feeds.yaml", "done", str(target))


# ---------------------------------------------------------------------------
# Step 6 — Create .env.pipeline
# ---------------------------------------------------------------------------

def create_env_file(
    project_dir: Path, guardian_key: str, openrouter_key: str
) -> SetupResult:
    """Create ``.env.pipeline`` with API keys and secure permissions."""
    _print_header("Step 6: .env.pipeline")
    env_path = project_dir / ".env.pipeline"

    if env_path.exists():
        if not _ask_yes_no(".env.pipeline already exists — overwrite?", default=False):
            _info("Kept existing .env.pipeline")
            return SetupResult(".env.pipeline", "skipped", "already exists")

    if not _ask_yes_no("Create .env.pipeline with your API keys?", default=True):
        _info("Skipped")
        return SetupResult(".env.pipeline", "skipped")

    lines = []
    if guardian_key:
        lines.append(f"GUARDIAN_API_KEY={guardian_key}")
    if openrouter_key:
        lines.append(f"OPENROUTER_API_KEY={openrouter_key}")
    if not lines:
        _warn("No API keys to write — skipped")
        return SetupResult(".env.pipeline", "skipped", "no keys")

    content = "\n".join(lines) + "\n"
    env_path.write_text(content, encoding="utf-8")
    os.chmod(env_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    _ok("Created .env.pipeline (chmod 600)")
    return SetupResult(".env.pipeline", "done", str(env_path))


# ---------------------------------------------------------------------------
# Step 7 — rmapi pairing + /News folder
# ---------------------------------------------------------------------------

def check_rmapi_setup() -> SetupResult:
    """Check rmapi pairing and create /News folder if missing."""
    _print_header("Step 7: rmapi pairing + /News folder")
    if not _ask_yes_no("Check rmapi pairing and /News folder?", default=True):
        _info("Skipped")
        return SetupResult("rmapi setup", "skipped")

    # Check pairing
    try:
        result = _run_command(["rmapi", "ls"], timeout=15)
        if result.returncode != 0:
            _fail("rmapi not paired — run 'rmapi' and paste your one-time code")
            _info("Get a code at: https://my.remarkable.com/device/desktop/connect")
            return SetupResult("rmapi setup", "failed", "not paired")
        _ok("rmapi is paired")
    except FileNotFoundError:
        _fail("rmapi not found — install it first (see Installation)")
        return SetupResult("rmapi setup", "failed", "not installed")
    except subprocess.TimeoutExpired:
        _fail("rmapi ls timed out")
        return SetupResult("rmapi setup", "failed", "timeout")
    except Exception as exc:  # noqa: BLE001
        _fail(f"rmapi check error: {exc}")
        return SetupResult("rmapi setup", "failed", str(exc))

    # Check /News folder
    try:
        result = _run_command(["rmapi", "ls", "/News"], timeout=15)
        if result.returncode == 0:
            _ok("/News folder exists")
            return SetupResult("rmapi setup", "done", "paired, /News exists")
        _info("/News folder not found — creating…")
        result = _run_command(["rmapi", "mkdir", "/News"], timeout=15)
        if result.returncode == 0:
            _ok("Created /News folder")
            return SetupResult("rmapi setup", "done", "paired, /News created")
        _fail(f"Failed to create /News folder: {(result.stderr or '').strip()}")
        return SetupResult("rmapi setup", "warning", "/News creation failed")
    except subprocess.TimeoutExpired:
        _fail("rmapi command timed out")
        return SetupResult("rmapi setup", "warning", "timeout")
    except Exception as exc:  # noqa: BLE001
        _fail(f"Error creating /News: {exc}")
        return SetupResult("rmapi setup", "warning", str(exc))


# ---------------------------------------------------------------------------
# Step 8 — systemd timer (Linux only)
# ---------------------------------------------------------------------------

def install_systemd_timer(project_dir: Path) -> SetupResult:
    """Offer to install the systemd ``--user`` timer (Linux only)."""
    _print_header("Step 8: systemd timer (Linux only)")
    if platform.system() != "Linux":
        _info(f"Not Linux ({platform.system()}) — skipped")
        return SetupResult("systemd timer", "skipped", "not Linux")
    if not _ask_yes_no("Install systemd --user timer?", default=False):
        _info("Skipped")
        return SetupResult("systemd timer", "skipped")

    user_systemd = Path.home() / ".config" / "systemd" / "user"
    user_systemd.mkdir(parents=True, exist_ok=True)

    service_src = project_dir / "systemd" / "remarkable-morning.service"
    timer_src = project_dir / "systemd" / "remarkable-morning.timer"

    if not service_src.exists() or not timer_src.exists():
        _fail("systemd unit files not found in systemd/ directory")
        return SetupResult("systemd timer", "failed", "unit files missing")

    shutil.copy2(service_src, user_systemd / "remarkable-morning.service")
    shutil.copy2(timer_src, user_systemd / "remarkable-morning.timer")
    _ok(f"Copied unit files to {user_systemd}")

    for cmd in (
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "remarkable-morning.timer"],
        ["systemctl", "--user", "start", "remarkable-morning.timer"],
    ):
        r = _run_command(cmd, timeout=15)
        if r.returncode != 0:
            _fail(f"Failed: {' '.join(cmd)}")
            return SetupResult("systemd timer", "failed", f"{' '.join(cmd)} failed")

    _ok("systemd timer installed and started (05:30 EDT daily)")
    return SetupResult("systemd timer", "done", "installed")


# ---------------------------------------------------------------------------
# Step 9 — Run pipeline once
# ---------------------------------------------------------------------------

def offer_run_pipeline(project_dir: Path) -> SetupResult:
    """Offer to run the pipeline once to verify the setup."""
    _print_header("Step 9: Run pipeline once")
    if not _ask_yes_no("Run the pipeline now to verify?", default=False):
        _info("Skipped")
        return SetupResult("Run pipeline", "skipped")

    # Load env from .env.pipeline if it exists
    env = dict(os.environ)
    env_file = project_dir / ".env.pipeline"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v

    r = _run_command(
        [sys.executable, "-m", "remarkable_morning.pipeline", "feeds.yaml"],
        timeout=120,
    )
    if r.returncode == 0:
        _ok("Pipeline ran successfully")
        return SetupResult("Run pipeline", "done", "exit 0")
    _fail(f"Pipeline failed (exit {r.returncode})")
    if r.stderr:
        _info(r.stderr[:500])
    return SetupResult("Run pipeline", "failed", f"exit {r.returncode}")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

_STATUS_MARK = {
    "done": _GREEN,
    "skipped": _DIM,
    "failed": _RED,
    "warning": _YELLOW,
}


def print_summary(results: list[SetupResult]) -> None:
    """Print the setup summary table."""
    print(f"\n{_c(_BOLD, '=== Setup Summary ===')}\n")
    for r in results:
        mark = _c(_STATUS_MARK.get(r.status, _YELLOW), "✓" if r.status == "done" else
                  "○" if r.status == "skipped" else "✗" if r.status == "failed" else "⚠")
        detail = f" — {r.detail}" if r.detail else ""
        print(f"  {mark} {r.name}{detail}")

    failed = sum(1 for r in results if r.status == "failed")
    print()
    if failed:
        print(f"  {_c(_RED, f'{failed} step(s) failed')} — see messages above")
    else:
        print(f"  {_c(_GREEN, 'Setup complete!')}")


# ---------------------------------------------------------------------------
# Main wizard
# ---------------------------------------------------------------------------

def main() -> int:
    """Run the interactive setup wizard.  Returns 0 on success."""
    print(f"{_c(_BOLD, 'Remarkable Morning — Setup Wizard')}")
    print("Each step is skippable — press Enter to skip.\n")

    project_dir = Path.cwd()
    state = WizardState()

    # Step 1: Python version
    state.results.append(check_python())

    # Step 2: rmapi installed?
    state.results.append(check_rmapi())

    # Step 3: Guardian key
    r, guardian_key = prompt_guardian_key()
    state.results.append(r)
    state.guardian_key = guardian_key

    # Step 4: OpenRouter key
    r, openrouter_key = prompt_openrouter_key()
    state.results.append(r)
    state.openrouter_key = openrouter_key

    # Step 5: feeds.yaml
    state.results.append(copy_feeds_config(project_dir))

    # Step 6: .env.pipeline
    state.results.append(
        create_env_file(project_dir, guardian_key, openrouter_key)
    )

    # Step 7: rmapi pairing + /News
    state.results.append(check_rmapi_setup())

    # Step 8: systemd timer
    state.results.append(install_systemd_timer(project_dir))

    # Step 9: run pipeline once
    state.results.append(offer_run_pipeline(project_dir))

    # Summary
    print_summary(state.results)

    failed = sum(1 for r in state.results if r.status == "failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

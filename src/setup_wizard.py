"""
setup_wizard.py — interactive first-run setup wizard.
Triggered when main.py is called with no arguments and data/ does not exist.
Coexists with bootstrap.py, which runs silently on every subsequent invocation.
"""
from pathlib import Path
import sys


def _ask(prompt: str, default_yes: bool = True) -> bool:
    hint = "[Y/n]" if default_yes else "[y/N]"
    answer = input(f"  {prompt} {hint}: ").strip().lower()
    if answer == "":
        return default_yes
    return answer == "y"


def _step(label: str) -> None:
    print(f"  {label}...", end=" ", flush=True)


def _ok() -> None:
    print("✓")


def needs_setup() -> bool:
    """Return True if this looks like a first run (data/ absent or empty)."""
    data = Path("data")
    if not data.exists():
        return True
    # If data/ exists but has no JSON files anywhere, also offer setup
    jsons = list(data.rglob("*.json"))
    csvs  = [p for p in data.glob("*.csv") if p.stat().st_size > 0]
    return not jsons and not csvs


def run_wizard() -> None:
    from rich.console import Console
    console = Console()

    console.print("\n  [bold]Innovation Hub — first-time setup[/bold]")
    console.print("  " + "─" * 42)

    if not _ask("No data directory found. Set up now?", default_yes=True):
        console.print("\n  Skipping setup. Run [bold]python main.py --help[/bold] to see available commands.\n")
        sys.exit(0)

    print()

    # ── Directory structure & seed files ─────────────────────────────────────
    from src.bootstrap import bootstrap
    _step("Creating directory structure and seed files")
    bootstrap(verbose=False)
    _ok()

    # ── Embedding model ───────────────────────────────────────────────────────
    print()
    console.print(
        "  The embedding model [bold]paraphrase-multilingual-mpnet-base-v2[/bold]"
        " (~400 MB) needs to be\n"
        "  downloaded once and will be cached to [dim]~/.cache/huggingface/[/dim].\n"
        "  After that, no network connection is ever needed."
    )
    if _ask("Download embedding model now?", default_yes=True):
        _step("Downloading model (this may take a few minutes)")
        try:
            from sentence_transformers import SentenceTransformer
            import tomllib
            with open("config.toml", "rb") as f:
                cfg = tomllib.load(f)
            SentenceTransformer(cfg["model"]["name"])
            _ok()
        except Exception as e:
            print(f"✗\n  [red]Download failed: {e}[/red]")
            console.print("  You can retry later — the model will download automatically on first use.")
    else:
        console.print("  [dim]Skipped — model will download automatically on first use.[/dim]")

    # ── Man pages ─────────────────────────────────────────────────────────────
    print()
    if _ask("Generate and install man pages?", default_yes=True):
        _step("Generating man pages")
        try:
            from main import build_parser
            from src.manpage import generate_all
            generate_all(build_parser())
            _ok()
        except Exception as e:
            print(f"✗\n  Man page generation failed: {e}")

        _step("Installing to ~/.local/share/man/man1/")
        try:
            import shutil
            man_src = Path("man")
            man_dst = Path.home() / ".local/share/man/man1"
            man_dst.mkdir(parents=True, exist_ok=True)
            for f in man_src.glob("*.1"):
                shutil.copy(f, man_dst / f.name)
            # Update man index if mandb is available
            import subprocess
            result = subprocess.run(
                ["mandb", str(Path.home() / ".local/share/man")],
                capture_output=True
            )
            _ok()
        except Exception as e:
            print(f"✗\n  Installation failed: {e}")
            console.print("  You can install manually with: [bold]bash install.sh[/bold]")
    else:
        console.print("  [dim]Skipped — run [bold]bash install.sh[/bold] at any time to install man pages.[/dim]")

    # ── Shell alias ───────────────────────────────────────────────────────────
    print()
    script_path = Path("main.py").resolve()
    bashrc      = Path.home() / ".bashrc"
    alias_line  = f"alias innovhub='python {script_path}'"

    # Check if alias already exists
    already_aliased = bashrc.exists() and alias_line in bashrc.read_text()

    if not already_aliased and _ask("Add 'innovhub' shell alias to ~/.bashrc?", default_yes=True):
        with open(bashrc, "a") as f:
            f.write(f"\n# Innovation Hub\n{alias_line}\n")
        console.print(f"  ✓  Alias added — run: [bold]source ~/.bashrc[/bold]")
    elif already_aliased:
        console.print("  [dim]Alias already present in ~/.bashrc — skipped.[/dim]")
    else:
        console.print(f"  [dim]Skipped — you can add manually:[/dim]  {alias_line}")

    # ── Done ──────────────────────────────────────────────────────────────────
    print()
    console.print("  [bold green]Setup complete.[/bold green]")
    console.print("  Run [bold]innovhub --help[/bold] (or [bold]python main.py --help[/bold]) to get started.\n")

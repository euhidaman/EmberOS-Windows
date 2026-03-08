"""CLI interface for EmberOS-Windows."""

import json
import sys
import time
from pathlib import Path

import click
import requests

ROOT_DIR = Path(__file__).resolve().parent.parent


def _api_base() -> str:
    """Get the agent API base URL."""
    try:
        cfg = ROOT_DIR / "config" / "default.json"
        if cfg.exists():
            with open(cfg, "r", encoding="utf-8") as f:
                data = json.load(f)
            host = data.get("server_host", "127.0.0.1")
            port = data.get("agent_api_port", 8766)
            return f"http://{host}:{port}"
    except Exception:
        pass
    return "http://127.0.0.1:8766"


def _service_name() -> str:
    try:
        cfg = ROOT_DIR / "config" / "default.json"
        if cfg.exists():
            with open(cfg, "r", encoding="utf-8") as f:
                return json.load(f).get("service_name", "EmberOSAgent")
    except Exception:
        pass
    return "EmberOSAgent"


@click.group()
def cli():
    """EmberOS-Windows — AI-powered agentic OS layer."""
    pass


@cli.command()
def start():
    """Start the EmberOS Windows Service."""
    import subprocess
    svc = _service_name()
    result = subprocess.run(["sc.exe", "start", svc], capture_output=True, text=True)
    if result.returncode == 0:
        click.echo(f"Service '{svc}' started.")
    else:
        click.echo(f"Failed to start service: {result.stderr.strip() or result.stdout.strip()}")
        click.echo("You may need to run as Administrator, or start manually:")
        venv_py = ROOT_DIR / "env" / "venv" / "Scripts" / "python.exe"
        click.echo(f'  {venv_py} -m emberos.service')


@cli.command()
def stop():
    """Stop the EmberOS Windows Service."""
    import subprocess
    svc = _service_name()
    result = subprocess.run(["sc.exe", "stop", svc], capture_output=True, text=True)
    if result.returncode == 0:
        click.echo(f"Service '{svc}' stopped.")
    else:
        click.echo(f"Failed to stop service: {result.stderr.strip() or result.stdout.strip()}")


@cli.command()
def status():
    """Show service, BitNet server, and hardware status."""
    base = _api_base()
    try:
        resp = requests.get(f"{base}/status", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            click.echo("EmberOS Status")
            click.echo("=" * 40)
            for k, v in data.items():
                click.echo(f"  {k}: {v}")
        else:
            click.echo(f"Status request failed: HTTP {resp.status_code}")
    except requests.ConnectionError:
        click.echo("Cannot connect to EmberOS service. Is it running?")
        # Try to show hardware.json at least
        hw = ROOT_DIR / "config" / "hardware.json"
        if hw.exists():
            with open(hw, "r", encoding="utf-8") as f:
                data = json.load(f)
            click.echo("\nHardware profile:")
            for k, v in data.items():
                click.echo(f"  {k}: {v}")


@cli.command()
def chat():
    """Interactive chat REPL with the EmberOS agent."""
    base = _api_base()
    click.echo("EmberOS Chat — type 'exit' or 'quit' to stop")
    click.echo("-" * 50)

    while True:
        try:
            user_input = click.prompt("\nYou", prompt_suffix="> ").strip()
        except (EOFError, KeyboardInterrupt, click.Abort):
            break
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            break

        try:
            resp = requests.post(
                f"{base}/query",
                json={"input": user_input},
                timeout=120,
            )
            if resp.status_code == 200:
                answer = resp.json().get("response", "(no response)")
                click.echo(f"\nEmberOS> {answer}")
            else:
                click.echo(f"\n[Error {resp.status_code}] {resp.text}")
        except requests.ConnectionError:
            click.echo("\n[Error] Cannot connect to service. Is it running?")
        except Exception as e:
            click.echo(f"\n[Error] {e}")

    click.echo("\nGoodbye!")


@cli.command("query")
@click.argument("text")
def query_cmd(text):
    """Send a single query to the EmberOS agent."""
    base = _api_base()
    try:
        resp = requests.post(
            f"{base}/query",
            json={"input": text},
            timeout=120,
        )
        if resp.status_code == 200:
            click.echo(resp.json().get("response", "(no response)"))
        else:
            click.echo(f"[Error {resp.status_code}] {resp.text}")
    except requests.ConnectionError:
        click.echo("Cannot connect to EmberOS service. Is it running?")
    except Exception as e:
        click.echo(f"[Error] {e}")


@cli.command()
def logs():
    """Tail the EmberOS log file."""
    log_file = ROOT_DIR / "logs" / "emberos.log"
    if not log_file.exists():
        click.echo("Log file not found yet.")
        return

    click.echo(f"Tailing {log_file} (Ctrl+C to stop)")
    click.echo("=" * 60)

    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            # Show last 50 lines
            lines = f.readlines()
            for line in lines[-50:]:
                click.echo(line, nl=False)

            # Tail new lines
            while True:
                line = f.readline()
                if line:
                    click.echo(line, nl=False)
                else:
                    time.sleep(0.5)
    except KeyboardInterrupt:
        click.echo("\n(stopped)")


@cli.command()
def install():
    """Register the EmberOS Windows Service."""
    import subprocess
    venv_py = ROOT_DIR / "env" / "venv" / "Scripts" / "python.exe"
    if not venv_py.exists():
        click.echo(f"Venv Python not found at {venv_py}. Run setup.ps1 first.")
        return
    result = subprocess.run(
        [str(venv_py), "-m", "emberos.service", "install"],
        capture_output=True, text=True, cwd=str(ROOT_DIR),
    )
    click.echo(result.stdout)
    if result.stderr:
        click.echo(result.stderr)


@cli.command()
def uninstall():
    """Unregister the EmberOS Windows Service."""
    import subprocess
    venv_py = ROOT_DIR / "env" / "venv" / "Scripts" / "python.exe"
    if not venv_py.exists():
        click.echo(f"Venv Python not found at {venv_py}. Run setup.ps1 first.")
        return
    result = subprocess.run(
        [str(venv_py), "-m", "emberos.service", "remove"],
        capture_output=True, text=True, cwd=str(ROOT_DIR),
    )
    click.echo(result.stdout)
    if result.stderr:
        click.echo(result.stderr)


# ── REPL mode ─────────────────────────────────────────────────────

_REPL_HELP = """\
EmberOS REPL Commands:
  :help       Show this help
  :status     Show agent / service status
  :clear      Clear the screen
  :exit       Exit the REPL (also Ctrl+C, Ctrl+D)
  :theme      Toggle dark/light theme (GUI only)
  :rollback   Undo last destructive file operation
  :snapshots  List recent file snapshots
"""


def _control_action(action: str) -> str:
    """Send a control action to the agent service."""
    base = _api_base()
    try:
        resp = requests.post(f"{base}/control", json={"action": action}, timeout=30)
        if resp.status_code == 200:
            return json.dumps(resp.json(), indent=2)
        return f"[Error {resp.status_code}] {resp.text}"
    except requests.ConnectionError:
        return "[Error] Cannot connect to EmberOS service."
    except Exception as e:
        return f"[Error] {e}"


def _repl_loop():
    """Interactive REPL with :commands and pipe support."""
    base = _api_base()
    is_tty = sys.stdin.isatty()

    if is_tty:
        click.echo("EmberOS REPL — type :help for commands, :exit to quit")
        click.echo("=" * 50)

    while True:
        # Read input
        try:
            if is_tty:
                user_input = input("ember> ").strip()
            else:
                line = sys.stdin.readline()
                if not line:
                    break
                user_input = line.strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue

        # Handle :commands
        if user_input.startswith(":"):
            cmd = user_input.lower()
            if cmd in (":exit", ":quit", ":q"):
                break
            elif cmd == ":help":
                click.echo(_REPL_HELP)
            elif cmd == ":status":
                try:
                    resp = requests.get(f"{base}/status", timeout=10)
                    if resp.status_code == 200:
                        for k, v in resp.json().items():
                            click.echo(f"  {k}: {v}")
                    else:
                        click.echo(f"[Error {resp.status_code}]")
                except Exception as e:
                    click.echo(f"[Error] {e}")
            elif cmd == ":clear":
                click.clear()
            elif cmd == ":rollback":
                click.echo(_control_action("rollback"))
            elif cmd == ":snapshots":
                try:
                    resp = requests.get(f"{base}/status", timeout=10)
                    if resp.status_code == 200:
                        data = resp.json()
                        click.echo(f"Has snapshots: {data.get('has_snapshots', False)}")
                except Exception as e:
                    click.echo(f"[Error] {e}")
            elif cmd == ":theme":
                click.echo("Theme toggling is available in the GUI only.")
            else:
                click.echo(f"Unknown command: {user_input}. Type :help for commands.")
            continue

        # Normal query
        try:
            resp = requests.post(
                f"{base}/query",
                json={"input": user_input},
                timeout=120,
            )
            if resp.status_code == 200:
                answer = resp.json().get("response", "(no response)")
                click.echo(f"\n{answer}\n")
            else:
                click.echo(f"\n[Error {resp.status_code}] {resp.text}\n")
        except requests.ConnectionError:
            click.echo("\n[Error] Cannot connect to service. Is it running?\n")
        except Exception as e:
            click.echo(f"\n[Error] {e}\n")

    if is_tty:
        click.echo("\nGoodbye!")


@cli.command()
def repl():
    """Start the interactive REPL (default mode)."""
    _repl_loop()


def main():
    # If no args provided, default to REPL mode
    if len(sys.argv) == 1:
        _repl_loop()
    else:
        cli()


if __name__ == "__main__":
    main()

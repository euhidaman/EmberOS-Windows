"""CLI interface for EmberOS-Windows — Rich-styled REPL with prompt-toolkit."""

import json
import sys
import threading
import time
from pathlib import Path

import click
import requests

ROOT_DIR = Path(__file__).resolve().parent.parent

# ── Ember colour palette ──────────────────────────────────────────
EMBER_ORANGE      = "#ff6b35"
EMBER_ORANGE_LITE = "#f7931e"
EMBER_TEXT        = "#f0f0fa"
EMBER_DIM         = "#a0a0b0"
EMBER_SUCCESS     = "#4caf50"
EMBER_WARNING     = "#ffc107"
EMBER_ERROR       = "#f44336"
EMBER_INFO        = "#64b5f6"
EMBER_DARK        = "#1a1a24"


# ── Helpers ───────────────────────────────────────────────────────

def _runtime_python() -> Path:
    embed_py = ROOT_DIR / "env" / "python-embed" / "python.exe"
    if embed_py.exists():
        return embed_py
    return ROOT_DIR / "env" / "venv" / "Scripts" / "python.exe"


def _api_base() -> str:
    try:
        cfg = ROOT_DIR / "config" / "default.json"
        if cfg.exists():
            with open(cfg, "r", encoding="utf-8") as f:
                data = json.load(f)
            return f"http://{data.get('server_host','127.0.0.1')}:{data.get('agent_api_port',8766)}"
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


def _control_action(action: str, base: str) -> dict:
    try:
        resp = requests.post(f"{base}/control", json={"action": action}, timeout=30)
        return resp.json() if resp.status_code == 200 else {"error": resp.text}
    except requests.ConnectionError:
        return {"error": "Cannot connect to EmberOS service."}
    except Exception as e:
        return {"error": str(e)}


# ── Click CLI commands ────────────────────────────────────────────

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
        click.echo(f"  {_runtime_python()} -m emberos.service")


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
    """Show service, runtime, and hardware status (rich panel)."""
    from rich.console import Console
    console = Console()
    _show_status(console, _api_base())


@cli.command("query")
@click.argument("text")
def query_cmd(text):
    """Send a single query to the EmberOS agent."""
    from rich.console import Console
    from rich.panel import Panel
    console = Console()
    base = _api_base()
    try:
        resp = requests.post(f"{base}/query", json={"input": text}, timeout=600)
        if resp.status_code == 200:
            answer = resp.json().get("response", "(no response)")
            console.print(Panel(answer, border_style=EMBER_SUCCESS, padding=(0, 1)))
        else:
            console.print(Panel(f"HTTP {resp.status_code}: {resp.text}",
                                title="Error", border_style=EMBER_ERROR))
    except requests.ConnectionError:
        console.print(f"[{EMBER_ERROR}]Cannot connect to EmberOS service. Is it running?[/{EMBER_ERROR}]")
    except Exception as e:
        console.print(f"[{EMBER_ERROR}]{e}[/{EMBER_ERROR}]")


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
            for line in f.readlines()[-50:]:
                click.echo(line, nl=False)
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
    runtime_py = _runtime_python()
    if not runtime_py.exists():
        click.echo(f"Python runtime not found at {runtime_py}. Run setup.ps1 first.")
        return
    result = subprocess.run(
        [str(runtime_py), "-m", "emberos.service", "install"],
        capture_output=True, text=True, cwd=str(ROOT_DIR),
    )
    click.echo(result.stdout)
    if result.stderr:
        click.echo(result.stderr)


@cli.command()
def uninstall():
    """Unregister the EmberOS Windows Service."""
    import subprocess
    runtime_py = _runtime_python()
    if not runtime_py.exists():
        click.echo(f"Python runtime not found at {runtime_py}. Run setup.ps1 first.")
        return
    result = subprocess.run(
        [str(runtime_py), "-m", "emberos.service", "remove"],
        capture_output=True, text=True, cwd=str(ROOT_DIR),
    )
    click.echo(result.stdout)
    if result.stderr:
        click.echo(result.stderr)


@cli.command()
def repl():
    """Start the interactive Rich REPL (default mode)."""
    _repl_loop()


# ── Rich REPL ─────────────────────────────────────────────────────

# Command registry: (name, aliases, description, usage)
_COMMANDS = [
    (":help",      ["h", "?"],           "Show this help or detailed help for a command",    ":help [command]"),
    (":status",    ["s"],                "Show EmberOS service and hardware status",          ":status"),
    (":tools",     ["t"],                "List available tools by category",                  ":tools"),
    (":context",   ["ctx"],              "Show active window, clipboard, CPU and RAM",        ":context"),
    (":history",   ["hist"],             "Show recent command history",                       ":history [n]"),
    (":snapshots", ["snap"],             "List file snapshots available for rollback",        ":snapshots"),
    (":rollback",  ["rb"],               "Restore previous state from the last snapshot",     ":rollback"),
    (":config",    ["cfg"],              "View current configuration",                        ":config [filter]"),
    (":clear",     ["cls"],              "Clear the terminal screen",                         ":clear"),
    (":quit",      ["exit", "q"],        "Exit the EmberOS REPL",                             ":quit"),
]

_TOOL_CATEGORIES = [
    ("File Operations", [
        ("find_files",       "Find files by name, type, or modification date"),
        ("organize_folder",  "Organize a folder by file type into subfolders"),
        ("move_file",        "Move a file or directory"),
        ("copy_file",        "Copy a file or directory"),
        ("rename_file",      "Rename a file or directory"),
        ("delete_file",      "Delete a file or directory (snapshot created first)"),
        ("get_file_info",    "Get file details: size, permissions, timestamps"),
        ("create_directory", "Create a new directory"),
        ("read_file",        "Read the contents of a text file"),
        ("write_file",       "Write content to a file"),
        ("list_dir",         "List directory contents"),
    ]),
    ("System Queries", [
        ("disk_usage",        "Get disk usage for all drives"),
        ("ram_status",        "Get RAM usage and available memory"),
        ("running_processes", "List running processes (optional filter)"),
        ("system_uptime",     "Get how long the system has been running"),
        ("cpu_info",          "Get CPU name, cores, frequency, and usage"),
        ("cpu_temperature",   "Get CPU temperature if available"),
        ("get_system_info",   "Get hardware profile snapshot"),
    ]),
    ("App & Window", [
        ("launch_app",       "Launch an application by name (calculator, browser, etc.)"),
        ("open_file",        "Open a file with its default application"),
        ("get_active_window","Get the currently active window title"),
        ("close_window",     "Close a window by its title"),
    ]),
    ("Shell & Process", [
        ("run_shell",    "Execute a shell command and return output"),
        ("kill_process", "Kill a process by name or PID"),
    ]),
    ("Clipboard & Web", [
        ("get_clipboard", "Read the current clipboard text"),
        ("set_clipboard", "Write text to the clipboard"),
        ("search_web",    "Open a URL in the default browser"),
    ]),
]


def _print_banner(console, connected: bool, status_data: dict):
    """Print the EmberOS startup banner."""
    from rich.text import Text
    from rich.panel import Panel

    # Compact, readable ASCII art
    art = (
        "███████╗███╗   ███╗██████╗ ███████╗██████╗        ██████╗  ██████╗\n"
        "██╔════╝████╗ ████║██╔══██╗██╔════╝██╔══██╗      ██╔═══██╗██╔════╝\n"
        "█████╗  ██╔████╔██║██████╔╝█████╗  ██████╔╝█████╗██║   ██║███████╗\n"
        "██╔══╝  ██║╚██╔╝██║██╔══██╗██╔══╝  ██╔══██╗╚════╝██║   ██║╚════██║\n"
        "███████╗██║ ╚═╝ ██║██████╔╝███████╗██║  ██║      ╚██████╔╝██████║\n"
        "╚══════╝╚═╝     ╚═╝╚═════╝ ╚══════╝╚═╝  ╚═╝       ╚═════╝ ╚═════╝"
    )

    console.print()
    console.print(Text(art, style=f"bold {EMBER_INFO}"))

    # Status line
    if connected:
        model   = status_data.get("model", "local")
        dot     = f"[{EMBER_SUCCESS}]●[/{EMBER_SUCCESS}]"
        info    = (f"  {dot} [{EMBER_SUCCESS}]Connected[/{EMBER_SUCCESS}]"
                   f"  [{EMBER_DIM}]Model:[/{EMBER_DIM}] {model}")
    else:
        dot  = f"[{EMBER_ERROR}]●[/{EMBER_ERROR}]"
        info = (f"  {dot} [{EMBER_ERROR}]Offline[/{EMBER_ERROR}]"
                f"  [{EMBER_DIM}](service not running — start with:[/{EMBER_DIM}]"
                f" [bold]emberos start[/bold][{EMBER_DIM}])[/{EMBER_DIM}]")

    console.print(f"  [bold {EMBER_ORANGE}]Windows AI Agent Layer[/bold {EMBER_ORANGE}]  —  v1.0")
    console.print(info)
    console.print(f"  [{EMBER_DIM}]Type [bold]:help[/bold] for commands · [bold]:quit[/bold] or Ctrl+D to exit[/{EMBER_DIM}]")
    console.print()


def _show_help(console, arg: str = ""):
    """Show rich help table or detailed command help."""
    from rich.table import Table
    from rich.panel import Panel

    if arg:
        # Detailed help for a specific command
        cmd_name = arg.lstrip(":")
        match = None
        for entry in _COMMANDS:
            name, aliases, desc, usage = entry
            if name[1:] == cmd_name or cmd_name in aliases:
                match = entry
                break
        if match:
            name, aliases, desc, usage = match
            body = (
                f"[bold]{name}[/bold]\n\n"
                f"{desc}\n\n"
                f"[{EMBER_DIM}]Usage:[/{EMBER_DIM}]   {usage}\n"
                f"[{EMBER_DIM}]Aliases:[/{EMBER_DIM}] {', '.join(':' + a for a in aliases)}"
            )
            console.print(Panel(body, title=f"Help: {name}",
                                border_style=EMBER_INFO, padding=(0, 2)))
        else:
            console.print(f"[{EMBER_ERROR}]Unknown command:[/{EMBER_ERROR}] :{cmd_name}")
        return

    # General help table
    table = Table(
        title=f"[bold {EMBER_ORANGE}]EmberOS Terminal Commands[/bold {EMBER_ORANGE}]",
        border_style=EMBER_ORANGE,
        header_style=f"bold {EMBER_ORANGE}",
        show_lines=True,
    )
    table.add_column("Command",     style=f"bold {EMBER_INFO}", min_width=12)
    table.add_column("Aliases",     style=EMBER_DIM,            min_width=14)
    table.add_column("Description",                             min_width=45)
    table.add_column("Usage",       style=EMBER_DIM)

    for name, aliases, desc, usage in _COMMANDS:
        table.add_row(name, ", ".join(":" + a for a in aliases), desc, usage)

    console.print(table)
    console.print()
    console.print(f"  [{EMBER_DIM}]Any other input is sent to the AI agent as a natural language query.[/{EMBER_DIM}]")
    console.print(f"  [{EMBER_DIM}]Pipe mode:  [bold]echo \"Find Python files modified today\" | ember[/bold][/{EMBER_DIM}]")
    console.print()


def _show_status(console, base: str):
    """Show rich status panel."""
    from rich.table import Table
    from rich.panel import Panel

    def _row(table, key, val, ok=None):
        if ok is True:
            val_str = f"[{EMBER_SUCCESS}]{val}[/{EMBER_SUCCESS}]"
        elif ok is False:
            val_str = f"[{EMBER_WARNING}]{val}[/{EMBER_WARNING}]"
        else:
            val_str = str(val)
        table.add_row(f"[{EMBER_DIM}]{key}[/{EMBER_DIM}]", val_str)

    try:
        resp = requests.get(f"{base}/status", timeout=5)
        if resp.status_code == 200:
            d = resp.json()
            t = Table(show_header=False, box=None, padding=(0, 2))
            t.add_column("key",   style=EMBER_DIM, min_width=22)
            t.add_column("value", min_width=30)

            svc = d.get("service", "?")
            _row(t, "Service",        svc,  ok=(svc == "running"))
            runtime = d.get("runtime_server", "?")
            _row(t, "Runtime server", runtime, ok=(runtime == "running"))
            _row(t, "GPU mode",       d.get("gpu_mode", "?"))
            _row(t, "Model",          d.get("model", "?"))
            _row(t, "Inference port", d.get("server_port", "?"))
            mem = d.get("memory_entries", 0)
            _row(t, "Memory entries", mem)
            snaps = d.get("has_snapshots", False)
            _row(t, "Snapshots",
                 "Available — use :rollback to restore" if snaps else "None",
                 ok=snaps if snaps else None)

            hw_path = ROOT_DIR / "config" / "hardware.json"
            if hw_path.exists():
                with open(hw_path, encoding="utf-8") as f:
                    hw = json.load(f)
                t.add_row("", "")
                _row(t, "CPU arch",   hw.get("cpu_arch", "?"))
                _row(t, "CPU cores",
                     f"{hw.get('cpu_cores','?')} physical / {hw.get('cpu_threads','?')} logical")
                _row(t, "RAM",        f"{hw.get('ram_gb','?')} GB")
                gpu_name = hw.get("gpu_name", "")
                if gpu_name:
                    vram = hw.get("gpu_vram_mb", 0)
                    _row(t, "GPU",  f"{gpu_name}  ({vram} MB VRAM)")
                if hw.get("cuda_version"):
                    _row(t, "CUDA", hw["cuda_version"])

            console.print(Panel(t, title=f"[bold {EMBER_ORANGE}]EmberOS Status[/bold {EMBER_ORANGE}]",
                                border_style=EMBER_ORANGE, padding=(0, 1)))
        else:
            console.print(Panel(f"HTTP {resp.status_code}: {resp.text}",
                                title="Status Error", border_style=EMBER_ERROR))

    except requests.ConnectionError:
        body = (f"[{EMBER_ERROR}]Service not running[/{EMBER_ERROR}]\n\n"
                f"Start with:  [bold]emberos start[/bold]\n"
                f"Or directly: [bold]{_runtime_python()} -m emberos.service[/bold]")
        hw_path = ROOT_DIR / "config" / "hardware.json"
        if hw_path.exists():
            with open(hw_path, encoding="utf-8") as f:
                hw = json.load(f)
            body += (f"\n\n[{EMBER_DIM}]Hardware:[/{EMBER_DIM}]  "
                     f"{hw.get('cpu_arch','?')}  ·  {hw.get('ram_gb','?')} GB RAM"
                     f"  ·  {hw.get('gpu_name','no GPU')}")
        console.print(Panel(body, title=f"[bold {EMBER_ORANGE}]EmberOS Status[/bold {EMBER_ORANGE}]",
                            border_style=EMBER_ERROR, padding=(0, 1)))

    except Exception as e:
        console.print(f"[{EMBER_ERROR}]Status error: {e}[/{EMBER_ERROR}]")


def _show_tools(console):
    """List available tools by category in rich tables."""
    from rich.table import Table

    for category, tools in _TOOL_CATEGORIES:
        t = Table(
            title=f"[bold]{category}[/bold]",
            border_style=EMBER_ORANGE,
            header_style=f"bold {EMBER_ORANGE}",
            show_lines=False,
            min_width=60,
        )
        t.add_column("Tool",        style=f"bold {EMBER_INFO}", min_width=22)
        t.add_column("Description", style=EMBER_TEXT)
        for name, desc in tools:
            t.add_row(name, desc)
        console.print(t)
        console.print()


def _show_context(console):
    """Show live system context: active window, clipboard, CPU, RAM."""
    from rich.tree import Tree
    from rich.panel import Panel

    active_win = "(unavailable)"
    clipboard  = "(unavailable)"
    cpu_pct    = 0.0
    ram_pct    = 0.0
    ram_avail  = 0.0

    try:
        import pygetwindow as gw
        w = gw.getActiveWindow()
        active_win = w.title if w else "(none)"
    except Exception:
        pass

    try:
        import pyperclip
        clip = pyperclip.paste() or "(empty)"
        clipboard = (clip[:80] + "…") if len(clip) > 80 else clip
    except Exception:
        pass

    try:
        import psutil
        cpu_pct   = psutil.cpu_percent(interval=0.5)
        vm        = psutil.virtual_memory()
        ram_pct   = vm.percent
        ram_avail = vm.available / (1024 ** 3)
    except Exception:
        pass

    tree = Tree(f"[bold {EMBER_ORANGE}]System Context[/bold {EMBER_ORANGE}]")
    tree.add(f"[{EMBER_INFO}]Active Window[/{EMBER_INFO}]  {active_win}")
    tree.add(f"[{EMBER_INFO}]Clipboard[/{EMBER_INFO}]      {clipboard}")
    tree.add(f"[{EMBER_INFO}]CPU Usage[/{EMBER_INFO}]      {cpu_pct:.1f}%")
    tree.add(f"[{EMBER_INFO}]RAM Usage[/{EMBER_INFO}]      {ram_pct:.0f}% used  ({ram_avail:.1f} GB available)")

    console.print(Panel(tree, border_style=EMBER_ORANGE, padding=(0, 1)))


def _show_history(console, history: list, arg: str = ""):
    """Show recent command history in a rich table."""
    from rich.table import Table

    try:
        n = int(arg) if arg else 20
    except ValueError:
        n = 20

    recent = history[-n:]
    if not recent:
        console.print(f"[{EMBER_DIM}]No history yet.[/{EMBER_DIM}]")
        return

    t = Table(
        title=f"[bold {EMBER_ORANGE}]Command History[/bold {EMBER_ORANGE}]",
        border_style=EMBER_ORANGE,
        header_style=f"bold {EMBER_ORANGE}",
    )
    t.add_column("#",       style=EMBER_DIM, min_width=4)
    t.add_column("Command", min_width=40)

    offset = max(0, len(history) - n)
    for i, cmd in enumerate(recent, start=offset + 1):
        style = EMBER_DIM if cmd.startswith(":") else EMBER_TEXT
        t.add_row(str(i), f"[{style}]{cmd}[/{style}]")

    console.print(t)


def _show_snapshots(console, base: str):
    """List file snapshots available for rollback."""
    from rich.table import Table
    from rich.panel import Panel
    from datetime import datetime

    try:
        # Check if any snapshots exist
        resp = requests.get(f"{base}/status", timeout=5)
        has_snaps = resp.json().get("has_snapshots", False) if resp.status_code == 200 else True
    except Exception:
        has_snaps = True  # Try anyway if offline

    try:
        sys.path.insert(0, str(ROOT_DIR))
        from emberos.snapshot import SnapshotManager
        mgr   = SnapshotManager()
        snaps = mgr.list_snapshots()
    except Exception as e:
        console.print(f"[{EMBER_ERROR}]Cannot read snapshots: {e}[/{EMBER_ERROR}]")
        return

    if not snaps:
        console.print(f"[{EMBER_DIM}]No snapshots available.[/{EMBER_DIM}]")
        return

    t = Table(
        title=f"[bold {EMBER_ORANGE}]File Snapshots[/bold {EMBER_ORANGE}]",
        border_style=EMBER_ORANGE,
        header_style=f"bold {EMBER_ORANGE}",
        show_lines=True,
    )
    t.add_column("Snapshot ID",   style=EMBER_DIM,  min_width=22)
    t.add_column("Original File", min_width=30)
    t.add_column("Operation",     style=EMBER_INFO,  min_width=10)
    t.add_column("Timestamp",     style=EMBER_DIM,   min_width=19)

    for snap in snaps[:15]:
        ts_raw = snap.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts_raw)
            ts = dt.strftime("%Y-%m-%d  %H:%M:%S")
        except Exception:
            ts = ts_raw[:19]

        orig = snap.get("original_path", "?")
        orig = ("…" + orig[-38:]) if len(orig) > 40 else orig
        sid  = snap.get("id", "")
        sid  = (sid[:20] + "…") if len(sid) > 22 else sid
        t.add_row(sid, orig, snap.get("operation", "?"), ts)

    console.print(t)
    console.print(f"  [{EMBER_DIM}]Use [bold]:rollback[/bold] to restore the most recent snapshot.[/{EMBER_DIM}]")


def _do_rollback(console, base: str):
    """Rollback the last snapshot."""
    from rich.panel import Panel

    try:
        resp = requests.post(f"{base}/control", json={"action": "rollback"}, timeout=15)
        if resp.status_code == 200:
            data   = resp.json()
            detail = data.get("detail", "Rollback complete.")
            console.print(Panel(
                f"[{EMBER_SUCCESS}]{detail}[/{EMBER_SUCCESS}]",
                title=f"[bold {EMBER_SUCCESS}]Rollback Complete[/bold {EMBER_SUCCESS}]",
                border_style=EMBER_SUCCESS,
                padding=(0, 1),
            ))
        else:
            console.print(Panel(resp.text, title="Rollback Failed", border_style=EMBER_ERROR))
    except requests.ConnectionError:
        console.print(f"[{EMBER_ERROR}]Cannot connect to EmberOS service.[/{EMBER_ERROR}]")
    except Exception as e:
        console.print(f"[{EMBER_ERROR}]Rollback error: {e}[/{EMBER_ERROR}]")


def _show_config(console, arg: str = ""):
    """Display current configuration."""
    from rich.table import Table

    cfg_path = ROOT_DIR / "config" / "default.json"
    if not cfg_path.exists():
        console.print(f"[{EMBER_ERROR}]Config file not found: {cfg_path}[/{EMBER_ERROR}]")
        return

    with open(cfg_path, encoding="utf-8") as f:
        data = json.load(f)

    t = Table(
        title=f"[bold {EMBER_ORANGE}]EmberOS Configuration[/bold {EMBER_ORANGE}]",
        border_style=EMBER_ORANGE,
        header_style=f"bold {EMBER_ORANGE}",
        show_lines=False,
    )
    t.add_column("Setting", style=f"bold {EMBER_INFO}", min_width=28)
    t.add_column("Value")

    SKIP = {"system_prompt"}
    for k, v in data.items():
        if k in SKIP:
            continue
        if arg and arg.lower() not in k.lower():
            continue
        t.add_row(k, str(v))

    console.print(t)
    console.print(f"  [{EMBER_DIM}]File: {cfg_path}[/{EMBER_DIM}]")


def _do_query(console, base: str, user_input: str):
    """Send a natural language query with a spinner, display styled response."""
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.panel import Panel

    result: dict = {}

    def _fetch():
        try:
            resp = requests.post(
                f"{base}/query",
                json={"input": user_input},
                timeout=600,
            )
            if resp.status_code == 200:
                result["ok"] = resp.json().get("response", "(no response)")
            else:
                result["err"] = f"HTTP {resp.status_code}: {resp.text}"
        except requests.ConnectionError:
            result["err"] = (
                "Cannot connect to EmberOS service.\n"
                "Start it with:  [bold]emberos start[/bold]"
            )
        except Exception as e:
            result["err"] = str(e)

    thread = threading.Thread(target=_fetch, daemon=True)
    thread.start()

    with Progress(
        SpinnerColumn(style=EMBER_ORANGE),
        TextColumn(f"[{EMBER_DIM}]EmberOS is thinking…[/{EMBER_DIM}]"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task("thinking", total=None)
        while thread.is_alive():
            thread.join(timeout=0.1)

    if "err" in result:
        console.print(Panel(
            result["err"],
            title=f"[bold {EMBER_ERROR}]Error[/bold {EMBER_ERROR}]",
            border_style=EMBER_ERROR,
            padding=(0, 1),
        ))
        return

    response = result.get("ok", "(no response)")

    # Detect confirmation request
    if response.rstrip().endswith("(yes/no)"):
        console.print(Panel(
            response,
            title=f"[bold {EMBER_WARNING}]Confirmation Required[/bold {EMBER_WARNING}]",
            border_style=EMBER_WARNING,
            padding=(0, 1),
        ))
    else:
        console.print(Panel(
            response,
            title=f"[bold {EMBER_ORANGE}]EmberOS[/bold {EMBER_ORANGE}]",
            border_style=EMBER_SUCCESS,
            padding=(0, 1),
        ))


def _pipe_loop(base: str):
    """Non-interactive pipe mode: read lines from stdin, print responses."""
    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        try:
            resp = requests.post(f"{base}/query", json={"input": text}, timeout=300)
            if resp.status_code == 200:
                print(resp.json().get("response", ""), flush=True)
            else:
                print(f"[Error {resp.status_code}] {resp.text}", file=sys.stderr, flush=True)
        except requests.ConnectionError:
            print("Cannot connect to EmberOS service.", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr, flush=True)


def _repl_loop():
    """Rich interactive REPL with prompt-toolkit history and auto-suggest."""
    from rich.console import Console
    from rich.panel import Panel

    base   = _api_base()
    is_tty = sys.stdin.isatty()

    if not is_tty:
        _pipe_loop(base)
        return

    console = Console()

    # Check connection and gather status
    connected   = False
    status_data = {}
    try:
        resp = requests.get(f"{base}/status", timeout=3)
        if resp.status_code == 200:
            connected   = True
            status_data = resp.json()
    except Exception:
        pass

    _print_banner(console, connected, status_data)

    # Set up prompt-toolkit session with file-backed history
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.styles import Style as PTStyle

    history_file = ROOT_DIR / "data" / "cli_history"
    history_file.parent.mkdir(parents=True, exist_ok=True)

    session = PromptSession(
        history=FileHistory(str(history_file)),
        style=PTStyle.from_dict({"prompt": f"{EMBER_ORANGE} bold"}),
    )

    cmd_history: list[str] = []

    while True:
        try:
            user_input = session.prompt("ember> ").strip()
        except KeyboardInterrupt:
            console.print(f"\n  [{EMBER_DIM}]Use [bold]:quit[/bold] to exit.[/{EMBER_DIM}]")
            continue
        except EOFError:
            break

        if not user_input:
            continue

        cmd_history.append(user_input)

        # ── Built-in :commands ─────────────────────────────────
        if user_input.startswith(":"):
            parts = user_input[1:].split(None, 1)
            cmd   = parts[0].lower() if parts else ""
            arg   = parts[1].strip() if len(parts) > 1 else ""

            if cmd in ("quit", "exit", "q"):
                break

            elif cmd in ("help", "h", "?"):
                _show_help(console, arg)

            elif cmd in ("status", "s"):
                _show_status(console, base)

            elif cmd in ("tools", "t"):
                _show_tools(console)

            elif cmd in ("context", "ctx"):
                _show_context(console)

            elif cmd in ("history", "hist"):
                _show_history(console, cmd_history, arg)

            elif cmd in ("snapshots", "snap"):
                _show_snapshots(console, base)

            elif cmd in ("rollback", "rb"):
                _do_rollback(console, base)

            elif cmd in ("config", "cfg"):
                _show_config(console, arg)

            elif cmd in ("clear", "cls"):
                console.clear()

            else:
                console.print(
                    f"[{EMBER_ERROR}]Unknown command:[/{EMBER_ERROR}] :{cmd}"
                    f"  [{EMBER_DIM}](type [bold]:help[/bold] for commands)[/{EMBER_DIM}]"
                )
            continue

        # ── Natural language query ────────────────────────────
        _do_query(console, base, user_input)

    # Goodbye
    console.print()
    console.print(Panel(
        f"[{EMBER_DIM}]Thank you for using EmberOS.  Goodbye![/{EMBER_DIM}]",
        border_style=EMBER_ORANGE,
        padding=(0, 2),
    ))
    console.print()


# ── Entry point ───────────────────────────────────────────────────

def main():
    """Default entry: REPL when called without args, Click CLI otherwise."""
    if len(sys.argv) == 1:
        _repl_loop()
    else:
        cli()


if __name__ == "__main__":
    main()

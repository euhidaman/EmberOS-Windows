"""EmberOS entry point — allows `python -m emberos`."""
import sys

def main():
    if len(sys.argv) > 1 and sys.argv[0].endswith("emberos"):
        # Invoked as `python -m emberos` — run CLI
        from emberos.cli import main as cli_main
        cli_main()
    else:
        from emberos.cli import main as cli_main
        cli_main()

main()

"""Typer CLI. Thin wrappers over the stage functions."""

import typer

app = typer.Typer(help="JobPilot — Phase 0 pipeline", no_args_is_help=True)


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo("jobpilot 0.1.0 (Phase 0)")


if __name__ == "__main__":
    app()

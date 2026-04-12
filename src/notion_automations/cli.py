"""CLI application for notion-automations."""

import typer

app = typer.Typer(
    name="notion-automations",
    help="Automation scripts to enhance personal notion usage",
    no_args_is_help=True,
)


def hello(name: str = typer.Option("World", help="Who to greet")) -> None:
    """Greet someone."""
    typer.echo(f"Hello, {name}!")


app.command()(hello)


if __name__ == "__main__":
    app()

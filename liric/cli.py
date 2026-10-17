"""Command line interface for liric."""

from pathlib import Path
from typing import Optional
import click
from rich.console import Console
from rich.table import Table

from liric import __version__
from liric.applier import ApplySummary, apply_lyrics_to_library
from liric.config import (
    Config,
    create_default_config,
    interactive_configure,
    load_config,
    resolve_config_path,
)
from liric.db import Database
from liric.fetcher import FetchSummary, fetch_lyrics_for_library
from liric.runner import PipelineSummary, run_pipeline
from liric.scanner import ScanSummary, scan_directory
from liric.watcher import watch_folder

console = Console()


@click.group("liric")
@click.option(
    "--config",
    "-c",
    type=click.Path(dir_okay=False),
    help="Path to config file.",
)
@click.version_option(version=__version__, prog_name="liric")
@click.pass_context
def main(ctx: click.Context, config: Optional[str]) -> None:
    """liric - Music library lyrics enrichment CLI tool."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config


def format_scan_table(summary: ScanSummary, dry_run: bool = False) -> Table:
    """Format library scan summary table."""
    table = Table(
        title=f"Scan Summary{' (Dry Run)' if dry_run else ''}",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="green")

    table.add_row("Total Scanned", str(summary.total_scanned))
    table.add_row("New Tracks", str(summary.new_tracks))
    table.add_row("Updated Tracks", str(summary.updated_tracks))
    table.add_row("Unchanged Tracks", str(summary.unchanged_tracks))
    table.add_row(
        "Failed Files",
        str(summary.failed_files),
        style="red" if summary.failed_files > 0 else "dim",
    )
    table.add_section()
    table.add_row("With Synced Lyrics", str(summary.with_synced))
    table.add_row("With Plain Lyrics", str(summary.with_plain))
    table.add_row(
        "With No Lyrics",
        str(summary.with_no_lyrics),
        style="yellow" if summary.with_no_lyrics > 0 else "green",
    )
    return table


def format_fetch_table(summary: FetchSummary, dry_run: bool = False) -> Table:
    """Format lyrics fetch summary table."""
    table = Table(
        title=f"Fetch Summary{' (Dry Run)' if dry_run else ''}",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="green")

    table.add_row("Total Processed", str(summary.total_processed))
    table.add_row("Lyrics Found", str(summary.found))
    table.add_row(
        "Not Found",
        str(summary.not_found),
        style="yellow" if summary.not_found > 0 else "dim",
    )
    table.add_row(
        "Errors",
        str(summary.errors),
        style="red" if summary.errors > 0 else "dim",
    )
    table.add_row("Skipped", str(summary.skipped))
    return table


def format_apply_table(summary: ApplySummary, dry_run: bool = False) -> Table:
    """Format lyrics apply summary table."""
    table = Table(
        title=f"Apply Summary{' (Dry Run)' if dry_run else ''}",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="green")

    table.add_row("Total Processed", str(summary.total_processed))
    table.add_row("Lyrics Applied", str(summary.applied))
    table.add_row("Skipped", str(summary.skipped))
    table.add_row(
        "Errors",
        str(summary.errors),
        style="red" if summary.errors > 0 else "dim",
    )
    return table


def _handle_init(
    ctx: click.Context, interactive: bool, force: bool
) -> None:
    config_path = ctx.obj.get("config_path") if ctx.obj else None
    target = resolve_config_path(config_path)

    if target.exists() and not force and not interactive:
        console.print(
            f"[yellow]Configuration file already exists at {target}. Use --force to overwrite or --interactive to reconfigure.[/yellow]"
        )
        return

    if interactive:
        console.print(f"[bold blue]Configuring liric ({target})...[/bold blue]")
        interactive_configure(target)
        console.print(f"[green]Configuration saved to {target}[/green]")
    else:
        create_default_config(target)
        console.print(f"[green]Default configuration created at {target}[/green]")


@main.command("init", help="Initialize configuration file (default or interactive).")
@click.option(
    "--interactive",
    "-i",
    is_flag=True,
    default=False,
    help="Run interactive configuration prompt.",
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    default=False,
    help="Overwrite existing configuration file.",
)
@click.pass_context
def init_cmd(ctx: click.Context, interactive: bool, force: bool) -> None:
    _handle_init(ctx, interactive, force)


@main.command("configure", help="Configure liric settings (alias of init).")
@click.option(
    "--interactive",
    "-i",
    is_flag=True,
    default=False,
    help="Run interactive configuration prompt.",
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    default=False,
    help="Overwrite existing configuration file.",
)
@click.pass_context
def configure_cmd(ctx: click.Context, interactive: bool, force: bool) -> None:
    _handle_init(ctx, interactive, force)


@main.command("clean", help="Clean database state.")
@click.option(
    "--dry-run",
    "-n",
    is_flag=True,
    default=False,
    help="Show how many tracks would be cleaned without modifying database.",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Confirm deletion without prompting.",
)
@click.pass_context
def clean_cmd(ctx: click.Context, dry_run: bool, yes: bool) -> None:
    config_path = ctx.obj.get("config_path") if ctx.obj else None
    try:
        cfg = load_config(config_path)
    except FileNotFoundError:
        cfg = Config()

    with Database(cfg.db_path) as db:
        if dry_run:
            count = db.clean(dry_run=True)
            console.print(
                f"[yellow]Dry run: {count} track(s) would be removed from the database.[/yellow]"
            )
            return

        count = db.count_tracks()
        if count == 0:
            console.print("[dim]Database is already empty (0 tracks).[/dim]")
            return

        if not yes:
            if not click.confirm(
                f"Are you sure you want to remove {count} track(s) from the database?"
            ):
                console.print("[yellow]Clean operation aborted.[/yellow]")
                return

        deleted = db.clean(dry_run=False)
        console.print(
            f"[green]Successfully removed {deleted} track(s) from the database.[/green]"
        )


@main.command("scan", help="Scan music directory for audio files and populate database.")
@click.argument("folder", required=False, type=click.Path(file_okay=False, dir_okay=True))
@click.option(
    "--dry-run",
    "-n",
    is_flag=True,
    default=False,
    help="Show scan results without saving to the database.",
)
@click.pass_context
def scan_cmd(ctx: click.Context, folder: Optional[str], dry_run: bool) -> None:
    config_path = ctx.obj.get("config_path") if ctx.obj else None
    try:
        cfg = load_config(config_path)
    except FileNotFoundError:
        cfg = Config()

    target_str = folder or cfg.music_dir or "."
    target_path = Path(target_str).expanduser().resolve()

    if not target_path.exists() or not target_path.is_dir():
        console.print(
            f"[red]Error: Music directory '{target_str}' does not exist or is not a directory.[/red]"
        )
        ctx.exit(1)

    if dry_run:
        console.print(
            f"[yellow]Dry-run mode: scanning '{target_path}' (no database changes will be saved)...[/yellow]"
        )
    else:
        console.print(f"[bold cyan]Scanning music directory:[/bold cyan] {target_path}")

    with Database(cfg.db_path) as db:
        summary = scan_directory(
            folder=target_path,
            db=db,
            dry_run=dry_run,
            console=console,
            show_progress=True,
        )

    console.print(format_scan_table(summary, dry_run=dry_run))


@main.command("fetch", help="Fetch lyrics from LRCLIB for tracks in the database.")
@click.option(
    "--dry-run",
    "-n",
    is_flag=True,
    default=False,
    help="Show fetch results without saving to the database.",
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    default=False,
    help="Refetch already fetched or not_found tracks.",
)
@click.option(
    "--limit",
    "-l",
    type=int,
    default=None,
    help="Limit number of tracks to fetch.",
)
@click.pass_context
def fetch_cmd(
    ctx: click.Context,
    dry_run: bool,
    force: bool,
    limit: Optional[int],
) -> None:
    config_path = ctx.obj.get("config_path") if ctx.obj else None
    try:
        cfg = load_config(config_path)
    except FileNotFoundError:
        cfg = Config()

    if dry_run:
        console.print(
            "[yellow]Dry-run mode: fetching lyrics without saving changes to the database...[/yellow]"
        )
    else:
        console.print("[bold cyan]Fetching lyrics from LRCLIB...[/bold cyan]")

    with Database(cfg.db_path) as db:
        summary = fetch_lyrics_for_library(
            db=db,
            config=cfg,
            dry_run=dry_run,
            force=force,
            limit=limit,
            console=console,
            show_progress=True,
        )

    console.print(format_fetch_table(summary, dry_run=dry_run))


@main.command("apply", help="Apply downloaded lyrics to audio files (embed or .lrc files).")
@click.option(
    "--dry-run",
    "-n",
    is_flag=True,
    default=False,
    help="Show apply results without modifying files or database.",
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    default=False,
    help="Re-apply lyrics to already applied tracks.",
)
@click.option(
    "--limit",
    "-l",
    type=int,
    default=None,
    help="Limit number of tracks to apply.",
)
@click.pass_context
def apply_cmd(
    ctx: click.Context,
    dry_run: bool,
    force: bool,
    limit: Optional[int],
) -> None:
    config_path = ctx.obj.get("config_path") if ctx.obj else None
    try:
        cfg = load_config(config_path)
    except FileNotFoundError:
        cfg = Config()

    if dry_run:
        console.print(
            "[yellow]Dry-run mode: applying lyrics without modifying files or database...[/yellow]"
        )
    else:
        console.print("[bold cyan]Applying lyrics to music library...[/bold cyan]")

    with Database(cfg.db_path) as db:
        summary = apply_lyrics_to_library(
            db=db,
            config=cfg,
            dry_run=dry_run,
            force=force,
            limit=limit,
            console=console,
            show_progress=True,
        )

    console.print(format_apply_table(summary, dry_run=dry_run))


@main.command("watch", help="Watch music directory for file changes and update lyrics automatically.")
@click.argument("folder", required=False, type=click.Path(file_okay=False, dir_okay=True))
@click.option(
    "--dry-run",
    "-n",
    is_flag=True,
    default=False,
    help="Watch folder without modifying files or database.",
)
@click.option(
    "--debounce",
    type=float,
    default=1.0,
    show_default=True,
    help="Debounce delay in seconds before processing a detected file.",
)
@click.pass_context
def watch_cmd(
    ctx: click.Context,
    folder: Optional[str],
    dry_run: bool,
    debounce: float,
) -> None:
    config_path = ctx.obj.get("config_path") if ctx.obj else None
    try:
        cfg = load_config(config_path)
    except FileNotFoundError:
        cfg = Config()

    target_str = folder or cfg.music_dir or "."
    target_path = Path(target_str).expanduser().resolve()

    if not target_path.exists() or not target_path.is_dir():
        console.print(
            f"[red]Error: Music directory '{target_str}' does not exist or is not a directory.[/red]"
        )
        ctx.exit(1)

    if dry_run:
        console.print(
            f"[yellow]Dry-run mode: watching '{target_path}' (no changes will be saved to files or database)...[/yellow]"
        )
    else:
        console.print(f"[bold cyan]Watching music directory:[/bold cyan] {target_path}")

    with Database(cfg.db_path) as db:
        try:
            watch_folder(
                folder=target_path,
                db=db,
                config=cfg,
                dry_run=dry_run,
                debounce_seconds=debounce,
                console=console,
            )
        except KeyboardInterrupt:
            pass


@main.command("run", help="Run full pipeline: scan, fetch, and apply.")
@click.argument("folder", required=False, type=click.Path(file_okay=False, dir_okay=True))
@click.option(
    "--dry-run",
    "-n",
    is_flag=True,
    default=False,
    help="Run pipeline without saving changes to files or database.",
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    default=False,
    help="Force refetch and reapply to already processed tracks.",
)
@click.option(
    "--limit",
    "-l",
    type=int,
    default=None,
    help="Limit number of tracks to fetch and apply.",
)
@click.pass_context
def run_cmd(
    ctx: click.Context,
    folder: Optional[str],
    dry_run: bool,
    force: bool,
    limit: Optional[int],
) -> None:
    config_path = ctx.obj.get("config_path") if ctx.obj else None
    try:
        cfg = load_config(config_path)
    except FileNotFoundError:
        cfg = Config()

    target_str = folder or cfg.music_dir or "."
    target_path = Path(target_str).expanduser().resolve()

    if not target_path.exists() or not target_path.is_dir():
        console.print(
            f"[red]Error: Music directory '{target_str}' does not exist or is not a directory.[/red]"
        )
        ctx.exit(1)

    if dry_run:
        console.print(
            f"[yellow]Dry-run mode: executing pipeline on '{target_path}' (no changes will be saved)...[/yellow]"
        )
    else:
        console.print(f"[bold cyan]Running lyrics pipeline on:[/bold cyan] {target_path}")

    with Database(cfg.db_path) as db:
        pipeline_summary = run_pipeline(
            folder=target_path,
            db=db,
            config=cfg,
            dry_run=dry_run,
            force=force,
            limit=limit,
            console=console,
            show_progress=True,
        )

    console.print("\n[bold green]Pipeline Execution Completed[/bold green]\n")
    console.print(format_scan_table(pipeline_summary.scan_summary, dry_run=dry_run))
    console.print(format_fetch_table(pipeline_summary.fetch_summary, dry_run=dry_run))
    console.print(format_apply_table(pipeline_summary.apply_summary, dry_run=dry_run))


@main.command("interactive", help="Interactively review and resolve lyric candidate matches.")
@click.pass_context
def interactive_cmd(ctx: click.Context) -> None:
    console.print("[yellow]'interactive' is not yet implemented in Phase 1.[/yellow]")


@main.command("review", help="Review and resolve lyric candidate matches (alias of interactive).")
@click.pass_context
def review_cmd(ctx: click.Context) -> None:
    console.print("[yellow]'review' is not yet implemented in Phase 1.[/yellow]")


if __name__ == "__main__":
    main()

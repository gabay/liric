"""Sequential pipeline runner for liric: scan, fetch, and apply."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from rich.console import Console

from liric.applier import ApplySummary, apply_lyrics_to_library
from liric.config import Config
from liric.db import Database
from liric.fetcher import FetchSummary, fetch_lyrics_for_library
from liric.lrclib import LrclibClient
from liric.scanner import ScanSummary, scan_directory


@dataclass
class PipelineSummary:
    """Summary of the complete lyrics processing pipeline."""

    scan_summary: ScanSummary
    fetch_summary: FetchSummary
    apply_summary: ApplySummary


def run_pipeline(
    folder: Path | str,
    db: Database,
    config: Config,
    dry_run: bool = False,
    force: bool = False,
    limit: Optional[int] = None,
    console: Optional[Console] = None,
    client: Optional[LrclibClient] = None,
    show_progress: bool = True,
) -> PipelineSummary:
    """Run sequential pipeline: scan directory, fetch lyrics, and apply lyrics."""
    c = console or Console()

    # Step 1: Scan
    c.print("[bold cyan]Step 1/3: Scanning music library...[/bold cyan]")
    scan_sum = scan_directory(
        folder=folder,
        db=db,
        dry_run=dry_run,
        console=c,
        show_progress=show_progress,
    )

    # Step 2: Fetch
    c.print("[bold cyan]Step 2/3: Fetching lyrics from LRCLIB...[/bold cyan]")
    fetch_sum = fetch_lyrics_for_library(
        db=db,
        config=config,
        dry_run=dry_run,
        force=force,
        limit=limit,
        console=c,
        show_progress=show_progress,
        client=client,
    )

    # Step 3: Apply
    c.print("[bold cyan]Step 3/3: Applying lyrics to audio files...[/bold cyan]")
    apply_sum = apply_lyrics_to_library(
        db=db,
        config=config,
        dry_run=dry_run,
        force=force,
        limit=limit,
        console=c,
        show_progress=show_progress,
    )

    return PipelineSummary(
        scan_summary=scan_sum,
        fetch_summary=fetch_sum,
        apply_summary=apply_sum,
    )

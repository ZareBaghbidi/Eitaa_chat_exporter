"""Terminal progress UI used while media files are downloaded."""

from __future__ import annotations

import sys
from types import TracebackType
from typing import Any, Self

try:  # rich is a dependency of eitaa-cli, but keep the exporter usable without it.
    from rich.console import Console
    from rich.markup import escape
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        TextColumn,
        TimeRemainingColumn,
        TransferSpeedColumn,
    )

    _RICH_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only in minimal environments
    Console = None  # type: ignore[misc,assignment]
    Progress = None  # type: ignore[misc,assignment]
    _RICH_AVAILABLE = False

    def escape(value: str) -> str:  # type: ignore[misc]
        return value


class ProgressReporter:
    """Show an overall bar plus a live per-file bar in the terminal.

    Each downloaded file gets its own bar that runs from 0% to 100%. Completed
    bars remain visible for one more file so a 100% state is actually readable.
    The reporter is deliberately silent when ``quiet`` is true, and it falls
    back to simple percentage lines when rich is unavailable or stderr is not a
    terminal.
    """

    def __init__(
        self,
        *,
        quiet: bool = False,
        total_files: int = 0,
        total_bytes: int = 0,
    ) -> None:
        self.quiet = quiet
        self.total_files = max(0, total_files)
        self.total_bytes = max(1, total_bytes)
        self._progress: Any = None
        self._overall: int | None = None
        self._task: int | None = None
        self._task_total = 1
        self._completed: list[int] = []
        self._index = 0
        self._advanced = 0
        self._plain_label = ""
        self._plain_total = 1
        self._plain_done = 0
        self._rich = bool(_RICH_AVAILABLE and not quiet and sys.stderr.isatty())
        if self._rich:
            self._progress = Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(bar_width=None),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                console=Console(stderr=True),
                transient=False,
                refresh_per_second=12,
                expand=True,
            )

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> Self:
        if self._progress is not None:
            self._progress.start()
            self._overall = self._progress.add_task(
                f"[bold]All files[/] (0/{self.total_files})",
                total=self.total_bytes,
            )
            self._task = None
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc, traceback
        if exc_type is None:
            self.finish_all()
        else:
            self.stop()

    # -- per-file API ------------------------------------------------------

    def begin_file(self, index: int, label: str, total: int) -> None:
        """Start a new per-file bar; ``index`` is 1-based."""

        self._index = index
        self._plain_label = label
        self._plain_total = max(1, total)
        self._plain_done = 0
        self._advanced = 0
        self._task_total = max(1, total)
        if self._progress is None:
            if not self.quiet and not sys.stderr.isatty():
                print(f"[{index}/{self.total_files}] {label} ...", file=sys.stderr)
            elif not self.quiet:
                sys.stderr.write(f"\r[{index}/{self.total_files}] {label}   0%")
                sys.stderr.flush()
            return

        # Keep at most one previously completed bar, so a 100% file stays
        # visible while its thumbnail downloads, without flooding the screen.
        while len(self._completed) > 1:
            old = self._completed.pop(0)
            self._progress.remove_task(old)
        self._task = self._progress.add_task(
            f"[cyan]{escape(label)}[/]",
            total=self._task_total,
        )
        if self._overall is not None:
            self._progress.update(
                self._overall,
                description=f"[bold]All files[/] ({index}/{self.total_files})",
            )

    def advance(self, amount: int) -> None:
        if amount <= 0:
            return
        if self._progress is None or self._task is None:
            self._plain_done = min(self._plain_total, self._plain_done + amount)
            if not self.quiet and sys.stderr.isatty():
                percent = round(self._plain_done * 100 / self._plain_total)
                sys.stderr.write(
                    f"\r[{self._index}/{self.total_files}] {self._plain_label} {percent:3d}%"
                )
                sys.stderr.flush()
            return
        self._progress.advance(self._task, amount)
        if self._overall is not None:
            self._progress.advance(self._overall, amount)
        self._advanced += amount

    def finish(self, *, success: bool = True, status: str = "done") -> None:
        if self._progress is None or self._task is None:
            if not self.quiet:
                suffix = "" if success else " (failed)"
                line = (
                    f"[{self._index}/{self.total_files}] {self._plain_label} "
                    f"100%{suffix}"
                )
                if sys.stderr.isatty():
                    sys.stderr.write("\r" + line + "\n")
                else:
                    print(line, file=sys.stderr)
                sys.stderr.flush()
            return
        task = self._task
        self._task = None
        marker = "[green]" + status + "[/]" if success else "[red]failed[/]"
        self._progress.update(
            task,
            completed=self._task_total,
            description=f"[cyan]{escape(self._plain_label)}[/] {marker}",
        )
        self._completed.append(task)
        if self._overall is not None and success and self._advanced < self._task_total:
            self._progress.advance(self._overall, self._task_total - self._advanced)

    def note(self, message: str) -> None:
        """Print a status line without corrupting the live progress display."""

        if self.quiet:
            return
        if self._progress is not None:
            self._progress.console.print(f"[yellow]{escape(message)}[/]")
        else:
            print(message, file=sys.stderr)

    def finish_all(self) -> None:
        if self._progress is not None:
            if self._overall is not None:
                self._progress.update(
                    self._overall,
                    completed=self.total_bytes,
                    description=f"[bold]All files[/] ({self.total_files}/{self.total_files})",
                )
            self.stop()
        else:
            self._task = None

    def stop(self) -> None:
        """Stop the live display without pretending the work completed."""

        if self._progress is not None:
            self._progress.stop()
            self._progress = None
        self._task = None

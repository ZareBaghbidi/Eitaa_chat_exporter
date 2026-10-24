from __future__ import annotations

from eitaa_export.progress import ProgressReporter


def test_quiet_reporter_prints_nothing(capsys) -> None:
    with ProgressReporter(quiet=True, total_files=2, total_bytes=2048) as reporter:
        reporter.begin_file(1, "photos/a.jpg", 1024)
        reporter.advance(512)
        reporter.finish()
        reporter.begin_file(2, "photos/a.jpg (thumbnail)", 1024)
        reporter.finish(status="cached")
        reporter.finish_all()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_plain_fallback_reaches_100_percent(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stderr.isatty", lambda: False)
    with ProgressReporter(total_files=1, total_bytes=100) as reporter:
        reporter.begin_file(1, "files/a.bin", 100)
        reporter.advance(40)
        reporter.advance(60)
        reporter.finish()
        reporter.finish_all()
    err = capsys.readouterr().err
    assert "files/a.bin" in err
    assert "100%" in err

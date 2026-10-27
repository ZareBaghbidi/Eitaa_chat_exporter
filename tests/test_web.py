from __future__ import annotations

from pathlib import Path

from eitaa_export.web import Job, WebProgress, _default_output_name, _slug


def test_slug_and_default_names() -> None:
    assert _slug("https://eitaa.com/yazd_oi") == "https___eitaa.com_yazd_oi"
    assert _slug("../../etc/passwd") == "etc_passwd"
    assert _default_output_name("https://eitaa.com/yazd_oi") == "yazd_oi"
    assert _default_output_name("https://eitaa.com/+AbCdEf") == "AbCdEf"
    assert _default_output_name("channel:123:456") == "channel_123_456"


def test_job_snapshot_percentages() -> None:
    job = Job(
        id="abc",
        target="@channel",
        title="Channel",
        output_dir=Path("/tmp/export"),
        overall_total=100,
        overall_done=42,
        files_total=4,
        files_done=2,
    )
    snapshot = job.snapshot()
    assert snapshot["overall"]["percent"] == 42.0
    assert snapshot["status"] == "running"
    job.status = "completed"
    job.overall_total = 0
    job.overall_done = 0
    assert job.snapshot()["overall"]["percent"] == 100.0


def test_web_progress_updates_job() -> None:
    job = Job(id="abc", target="@channel", title="Channel", output_dir=Path("/tmp/export"))
    progress = WebProgress(job, 2, 100)
    progress.begin_file(1, "photos/a.jpg", 60)
    progress.advance(30)
    assert job.file_done == 30
    assert job.overall_done == 30
    progress.finish()
    assert job.file_done == 60
    assert job.overall_done == 60
    assert job.files_done == 1

    progress.begin_file(2, "photos/b.jpg", 40)
    progress.finish(status="cached")
    assert job.overall_done == 100
    assert job.files_done == 2
    progress.finish_all()
    assert job.overall_done == job.overall_total

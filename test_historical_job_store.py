from historical_job_store import HistoricalJobStore


def test_job_store_round_trip(tmp_path):
    store = HistoricalJobStore(str(tmp_path / "jobs.sqlite3"))
    job = {
        "job_id": "abc123",
        "status": "running",
        "created_at": "2026-08-12T00:00:00+00:00",
        "updated_at": "2026-08-12T00:00:01+00:00",
        "request": {"start_date": "2026-07-01", "end_date": "2026-07-31"},
        "progress": {"days_processed": 7, "percent": 31},
        "checkpoint": {"last_completed_date": "2026-07-07", "daily_results": [{"date": "2026-07-07"}]},
        "result": None,
        "error": None,
        "persistence_note": "test",
    }
    store.upsert(job)
    loaded = store.get("abc123")
    assert loaded["status"] == "running"
    assert loaded["progress"]["days_processed"] == 7
    assert loaded["checkpoint"]["last_completed_date"] == "2026-07-07"
    assert store.resumable()[0]["job_id"] == "abc123"

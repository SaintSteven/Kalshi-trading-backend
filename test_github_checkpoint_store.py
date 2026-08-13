from github_checkpoint_store import GitHubCheckpointStore
from historical_job_store import HistoricalJobStore


def test_github_checkpoint_payload_round_trip(monkeypatch):
    monkeypatch.setenv('GITHUB_CHECKPOINT_TOKEN', 'test-token')
    store = GitHubCheckpointStore()
    payload = {'job_id': 'abc', 'checkpoint': {'last_completed_date': '2026-07-03'}, 'rows': list(range(20))}
    assert store._decode(store._encode(payload)) == payload


def test_github_checkpoint_status_reports_external_when_configured(monkeypatch):
    monkeypatch.setenv('GITHUB_CHECKPOINT_TOKEN', 'test-token')
    monkeypatch.setenv('GITHUB_CHECKPOINT_REPO', 'owner/repo')
    monkeypatch.setenv('GITHUB_CHECKPOINT_BRANCH', 'backtest-checkpoints')
    status = GitHubCheckpointStore().status()
    assert status['enabled'] is True
    assert status['durability'] == 'external'
    assert status['repository'] == 'owner/repo'


def test_local_store_can_recover_missing_job_from_remote(tmp_path, monkeypatch):
    monkeypatch.delenv('GITHUB_CHECKPOINT_TOKEN', raising=False)
    store = HistoricalJobStore(str(tmp_path / 'jobs.sqlite3'))
    expected = {
        'job_id': 'remote123', 'status': 'running', 'created_at': '2026-08-12T00:00:00+00:00',
        'updated_at': '2026-08-12T00:00:01+00:00', 'started_at': None, 'finished_at': None,
        'request': {'start_date': '2026-07-01', 'end_date': '2026-07-31'},
        'progress': {'days_processed': 4}, 'result': None,
        'checkpoint': {'last_completed_date': '2026-07-04'}, 'error': None,
        'persistence_note': 'github mirror',
    }
    monkeypatch.setattr(store.github, 'get', lambda job_id: expected if job_id == 'remote123' else None)
    recovered = store.get('remote123')
    assert recovered['checkpoint']['last_completed_date'] == '2026-07-04'
    assert store._get_local('remote123')['job_id'] == 'remote123'

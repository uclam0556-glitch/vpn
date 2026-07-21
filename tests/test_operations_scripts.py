import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_production_monitor_is_valid_bash_and_checks_backup_freshness() -> None:
    script = ROOT / "infra" / "monitor.sh"
    result = subprocess.run(
        ["bash", "-n", str(script)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    source = script.read_text()
    assert "hamalivpn-backup.timer" in source
    assert "systemctl show hamalivpn-backup.service -p Result" in source
    assert "backup_recent hamalivpn" in source
    assert "backup_recent remnawave" in source


def test_local_restore_drill_is_valid_bash_and_uses_disposable_databases() -> None:
    script = ROOT / "infra" / "verify-local-backup-restore.sh"
    result = subprocess.run(
        ["bash", "-n", str(script)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    source = script.read_text()
    assert "docker run -d --rm" in source
    assert "pg_isready" in source
    assert "ON_ERROR_STOP=1" in source
    assert "information_schema.tables" in source

"""Regression checks for the test environment isolation boundary."""
import os
from pathlib import Path
import subprocess
import sys

from app.core.config import Settings


def test_real_env_is_disabled_in_pytest_and_children(tmp_path):
    assert Settings.model_config["env_file"] is None
    script = "from app.core.config import Settings; print(Settings.model_config['env_file'])"
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "None"


def test_failure_report_does_not_echo_synthetic_credentials(tmp_path):
    conftest = Path(__file__).with_name("conftest.py")
    (tmp_path / "conftest.py").write_text(conftest.read_text(encoding="utf8"), encoding="utf8")
    (tmp_path / "test_failure.py").write_text(
        "def test_failure():\n    raise ValueError('postgresql://user:synthetic-secret@example.invalid/db')\n",
        encoding="utf8",
    )
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", str(tmp_path)],
                            capture_output=True, text=True, env=os.environ.copy())
    assert result.returncode == 1
    assert "synthetic-secret" not in result.stdout + result.stderr
    assert "Failure details suppressed" in result.stdout

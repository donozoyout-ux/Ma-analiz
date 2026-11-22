"""Fail before importing application/SQL/SDK code for unsafe test harness input."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.parametrize("changes,env_file,code",[
    ({"BETA_INTEGRATION_TEST":""},"","SYNTHETIC_TEST_OPT_IN_REQUIRED"),
    ({"DATABASE_URL":"SYNTHETIC_NEVER_CONNECT"},"","STORAGE_OR_CREDENTIAL_ENV_FORBIDDEN"),
    ({"R2_ENDPOINT":"SYNTHETIC_NEVER_CONNECT"},"","STORAGE_OR_CREDENTIAL_ENV_FORBIDDEN"),
    ({"GROQ_API_KEY":"SYNTHETIC_SECRET"},"","STORAGE_OR_CREDENTIAL_ENV_FORBIDDEN"),
    ({"PGHOST":"SYNTHETIC_NEVER_CONNECT"},"","STORAGE_OR_CREDENTIAL_ENV_FORBIDDEN"),
    ({"AUTO_RETENTION_DELETE_ENABLED":"true"},"","DELETE_GATES_MUST_BE_FALSE"),
    ({"OPERATIONAL_DELETE_ENABLED":"true"},"","DELETE_GATES_MUST_BE_FALSE"),
    ({},"DATABASE_URL=SYNTHETIC_NEVER_CONNECT\n","NONEMPTY_ENV_FILE_FORBIDDEN"),
])
def test_integration_harness_refuses_unsafe_environment_before_app_import(tmp_path,changes,env_file,code):
    script=tmp_path/"backend"/"tests"/"beta_integration_server.py"
    script.parent.mkdir(parents=True)
    shutil.copyfile(Path(__file__).with_name("beta_integration_server.py"),script)
    (tmp_path/".env").write_text(env_file,encoding="utf-8")
    env={key:value for key,value in os.environ.items() if key.upper() in {"SYSTEMROOT","WINDIR","TEMP","TMP","PATH"}}
    env.update(BETA_INTEGRATION_TEST="true",AUTO_RETENTION_DELETE_ENABLED="false",OPERATIONAL_DELETE_ENABLED="false")
    env.update(changes)
    result=subprocess.run([sys.executable,str(script)],env=env,cwd=tmp_path,capture_output=True,text=True,timeout=5)
    assert result.returncode!=0 and code in result.stderr
    assert not result.stdout and "SYNTHETIC_NEVER_CONNECT" not in result.stderr and "SYNTHETIC_SECRET" not in result.stderr

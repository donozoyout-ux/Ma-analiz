"""Run the opt-in synthetic HTTP chain with clean envs and managed local services.

No production .env/credentials are copied; the frontend must already be built
with its default loopback rewrite. All processes are stopped on every outcome.
"""
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile


def main():
    repo=Path(__file__).resolve().parents[2]
    frontend=repo/"frontend"
    manifest=json.loads((frontend/".next/routes-manifest.json").read_text(encoding="utf-8"))
    rewrite=manifest["rewrites"]
    rows=rewrite if isinstance(rewrite,list) else [row for group in rewrite.values() for row in group]
    if not any(row["source"]=="/api/v1/:path*" and row["destination"]=="http://127.0.0.1:8000/api/v1/:path*" for row in rows) or any(not row["destination"].startswith("http://127.0.0.1:8000/") for row in rows):
        raise RuntimeError("LOCAL_PROXY_BUILD_REQUIRED")
    for port in (8000,3106):
        with socket.socket() as check:
            if check.connect_ex(("127.0.0.1",port))==0:raise RuntimeError("LOCAL_TEST_PORT_BUSY")
    allow={"PATH","SYSTEMROOT","WINDIR","TEMP","TMP","USERPROFILE","LOCALAPPDATA","APPDATA","COMSPEC","PATHEXT","PROGRAMFILES","PROGRAMFILES(X86)","LANG","CHROME_PATH"}
    env={key:value for key,value in os.environ.items() if key.upper() in allow}
    env.update(AUTO_RETENTION_DELETE_ENABLED="false",OPERATIONAL_DELETE_ENABLED="false",SAFE_TO_DELETE="NO",LIVE_DELETE="NOT_RUN")
    # git archive uses committed source only, with no untracked .env or user files.
    source=subprocess.check_output(["git","-C",str(repo),"archive","--format=zip","HEAD"],env=env)
    with tempfile.TemporaryDirectory(prefix="ma-beta-managed-") as directory:
        root=Path(directory).resolve()
        assert root.parent==Path(tempfile.gettempdir()).resolve() and root.name.startswith("ma-beta-managed-")
        zipfile.ZipFile(io.BytesIO(source)).extractall(root)
        (root/".env").write_text("",encoding="utf-8")
        # Permit testing the new harness before its own test-only commit.
        shutil.copyfile(Path(__file__).with_name("beta_integration_server.py"),root/"backend/tests/beta_integration_server.py")
        backenv={**env,"BETA_INTEGRATION_TEST":"true","BETA_TEST_PORT":"8000","PYTHONPATH":str(root/"backend")}
        frontenv={**env,"NODE_ENV":"production","HOSTNAME":"127.0.0.1","PORT":"3106"}
        processes=[]
        flags=getattr(subprocess,"CREATE_NO_WINDOW",0)
        try:
            with (root/"backend.log").open("w",encoding="utf-8") as backlog,(root/"frontend.log").open("w",encoding="utf-8") as frontlog:
                processes.append(subprocess.Popen([sys.executable,"tests/beta_integration_server.py"],cwd=root/"backend",env=backenv,stdout=backlog,stderr=subprocess.STDOUT,creationflags=flags))
                processes.append(subprocess.Popen(["node",".next/standalone/server.js"],cwd=frontend,env=frontenv,stdout=frontlog,stderr=subprocess.STDOUT,creationflags=flags))
                # No proxy environment is inherited; only fixed loopback health URLs.
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                for _ in range(100):
                    if any(p.poll() is not None for p in processes):raise RuntimeError("LOCAL_TEST_SERVICE_FAILED")
                    try:
                        with opener.open("http://127.0.0.1:8000/__beta_test__/status",timeout=1) as response:
                            assert json.load(response)["evidence"]=="ISOLATED_SYNTHETIC_ONLY"
                        with opener.open("http://127.0.0.1:3106/health/ready",timeout=1) as response:
                            assert response.status==200
                        break
                    except (OSError,AssertionError):time.sleep(.2)
                else:raise RuntimeError("LOCAL_TEST_SERVICE_TIMEOUT")
                result=subprocess.run(["node","scripts/test-beta-integration-browser.mjs"],cwd=frontend,env=env,creationflags=flags,capture_output=True,text=True,encoding="utf-8",errors="replace")
                if result.returncode==0:
                    evidence=json.loads((frontend/"reports/beta-v0-integration/integration-browser-report.json").read_text(encoding="utf-8"))
                    assert evidence["production_requests"]==0 and evidence["SAFE_TO_DELETE"]=="NO"
                    print(json.dumps({"managed_local_integration":"PASS","checks":evidence["passed"],"production_requests":0,"LIVE_DELETE":"NOT_RUN"}))
                else:print("ISOLATED_BROWSER_INTEGRATION_FAILED",file=sys.stderr)
                return result.returncode
        finally:
            for process in reversed(processes):
                if process.poll() is None:process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)


if __name__=="__main__":
    try:raise SystemExit(main())
    except Exception:
        # No config values, URLs, child logs or traceback on launcher failure.
        print("ISOLATED_INTEGRATION_LAUNCH_FAILED",file=sys.stderr)
        raise SystemExit(1)

"""Offline tests for a bounded, one-shot nine-file scrape. No real network or R2."""
import csv
from io import StringIO
import json

import httpx
import pytest

from app.jobs.discipline_source_download import (
    SCOPE, SourceDownloadBlocked, download_all, fetch_one,
)
from app.jobs.discipline_batch_upload import load_batch_plan


def csv_bytes(season, league):
    start = int("20" + season[:2])
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Div","Date","HomeTeam","AwayTeam","HC","AC","HY","AY","HR","AR"])
    writer.writerow([league,f"20/08/{start}",f"TEST HOME {league}",f"TEST AWAY {league}",
                     0,8,2,"",0,1])
    return buf.getvalue().encode()


def make_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_fetch_one_rejects_redirect_and_ratelimit_without_retry():
    for status in (302, 401, 403, 429):
        hit = []
        with make_client(lambda req: hit.append(req) or httpx.Response(status,
                                headers={"Location":"https://example.invalid"})) as client:
            with pytest.raises(SourceDownloadBlocked,match="SOURCE_"):
                fetch_one(client,season="2425",league="E0")
        assert len(hit) == 1


def test_unauthorized_download_fails_before_http(tmp_path):
    with make_client(lambda req: pytest.fail("NO_NETWORK_WITHOUT_RIGHTS")) as client:
        with pytest.raises(SourceDownloadBlocked,match="PERMISSION"):
            download_all(directory=tmp_path,permission_reference="PENDING-PROVIDER-REPLY",
                         client=client,pause_seconds=0)


def test_nine_files_have_strict_urls_sha_samples_and_never_upload(tmp_path):
    captured=[]
    def handler(req):
        captured.append(str(req.url))
        parts=str(req.url).split("/")
        season,league=parts[-2],parts[-1].removesuffix(".csv")
        return httpx.Response(200,headers={"Content-Type":"text/csv"},
                              content=csv_bytes(season,league))
    with make_client(handler) as client:
        manifest=download_all(directory=tmp_path,
                              permission_reference="TEST-NONPRODUCTION-MOCK-ONLY",
                              client=client,pause_seconds=0,sleep=lambda _:pytest.fail("NO_SLEEP"))
    assert len(captured)==len(SCOPE)==9
    assert captured[0]=="https://www.football-data.co.uk/mmz4281/2425/E0.csv"
    assert captured[-1]=="https://www.football-data.co.uk/mmz4281/2627/SP1.csv"
    assert manifest["received_files"]==9 and manifest["r2_uploaded"] is False
    assert all(x["matches_with_any_stat"]==1 and x["complete_yellows"]==0
               for x in manifest["files"])
    plans=load_batch_plan(tmp_path,rights_ref="TEST-NONPRODUCTION-MOCK-ONLY")
    assert len(plans)==9
    assert all(len(p["rows"])==1 for p in plans)
    assert not (tmp_path/"r2-uploaded").exists()
    with make_client(lambda req:pytest.fail("NO_NETWORK_EXISTING_DATA")) as client:
        with pytest.raises(SourceDownloadBlocked,match="ALREADY"):
            download_all(directory=tmp_path,permission_reference="TEST-NONPRODUCTION-MOCK-ONLY",
                         client=client,pause_seconds=0)


def test_changing_one_input_invalidates_every_write(tmp_path):
    def handler(req):
        parts=str(req.url).split("/")
        return httpx.Response(200,headers={"Content-Type":"text/csv"},
                              content=csv_bytes(parts[-2],parts[-1].removesuffix(".csv")))
    with make_client(handler) as client:
        download_all(directory=tmp_path,permission_reference="TEST-NONPRODUCTION-MOCK-ONLY",
                     client=client,pause_seconds=0)
    path=tmp_path/"2627_D1.csv"
    path.write_bytes(path.read_bytes().replace(b",8,2,",b",9,2,"))
    with pytest.raises(ValueError,match="HASH_OR_COVERAGE_CHANGED"):
        load_batch_plan(tmp_path,rights_ref="TEST-NONPRODUCTION-MOCK-ONLY")


@pytest.mark.parametrize("payload,mime",[
    (b"<html>blocked</html>","text/html"),
    (b"garbage","text/csv"),
    (b"","text/csv"),
    (b"0"*(3_000_001),"text/csv"),
])
def test_invalid_source_never_passes(payload,mime):
    with make_client(lambda _req: httpx.Response(200,
                            headers={"Content-Type":mime},content=payload)) as client:
        with pytest.raises(SourceDownloadBlocked):
            fetch_one(client,season="2425",league="D1")


def test_batch_plan_refuses_symlink_directory(tmp_path):
    """A manifest cannot be substituted through a directory symlink."""
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable in this test environment")
    with pytest.raises(ValueError, match="SOURCE_DIRECTORY_SYMLINK_REFUSED"):
        load_batch_plan(link, rights_ref="TEST-NONPRODUCTION-MOCK-ONLY")


def test_batch_upload_never_initializes_r2_without_confirmed_bucket(tmp_path, capsys):
    """Even a well-formed nine-file manifest must not trigger a write."""
    from app.jobs.discipline_batch_upload import main as upload_main

    def handler(req):
        parts = str(req.url).split("/")
        return httpx.Response(200, headers={"Content-Type": "text/csv"},
                              content=csv_bytes(parts[-2], parts[-1].removesuffix(".csv")))

    rights = "TEST-NONPRODUCTION-MOCK-ONLY"
    with make_client(handler) as client:
        download_all(directory=tmp_path, permission_reference=rights, client=client,
                     pause_seconds=0)
    result = upload_main([
        "--directory", str(tmp_path), "--permission-reference", rights,
        "--apply", "--acknowledge-source-terms", "--confirm-r2-upload",
        "--confirm-database-name", "isolated_test_database",
        # --confirm-r2-bucket intentionally absent
    ])
    assert result == 2
    assert "LIVE_PERMISSION_TARGET_AND_R2_CONFIRMATION_REQUIRED" in capsys.readouterr().out

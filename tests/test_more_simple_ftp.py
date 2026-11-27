"""Tests for the More Simple FTP app's non-GUI parts: parsing, settings, and
the background transfer worker against a real local FTP server.

Set MSFTP_TEST_SERVER=host:port:user:password:/server/root/dir to run the
worker tests against an existing server instead of pyftpdlib.
"""
import hashlib
import os
import queue
import threading
import time

import pytest

tk = pytest.importorskip("tkinter")
import more_simple_ftp as m  # noqa: E402


# --- parsing -----------------------------------------------------------

def test_parse_unix_list_line():
    e = m.parse_list_line("-rw-r--r--    1 1000  1000   12345 Jan 05  2024 report final.pdf")
    assert (e.name, e.is_dir, e.size) == ("report final.pdf", False, 12345)
    d = m.parse_list_line("drwxr-xr-x    2 1000  1000    4096 Mar 10 14:22 photos")
    assert (d.name, d.is_dir, d.size) == ("photos", True, None)


def test_parse_symlink_and_dos_lines():
    link = m.parse_list_line("lrwxrwxrwx 1 0 0 7 Jan 01 2024 www -> /var/www")
    assert link.name == "www" and link.is_dir
    dos = m.parse_list_line("01-31-24  10:15AM       <DIR>          Backups")
    assert dos.name == "Backups" and dos.is_dir
    f = m.parse_list_line("01-31-24  10:15PM              2048 notes.txt")
    assert f.name == "notes.txt" and f.size == 2048


def test_mlsd_time_is_utc():
    ts = m.parse_mlsd_time("20240105120000")
    assert ts == 1704456000.0


def test_human_size():
    assert m.human_size(10) == "10 B"
    assert m.human_size(6 * 1024 * 1024) == "6.0 MB"


# --- settings ----------------------------------------------------------

def test_settings_roundtrip_and_rename(tmp_path):
    path = tmp_path / "cfg.json"
    s = m.Settings(str(path))
    s.put_server({**m.DEFAULT_SERVER, "name": "Office", "host": "ftp.example.com"})
    s.put_server({**m.DEFAULT_SERVER, "name": "Home", "host": "10.0.0.2", "port": 2121})
    s.put_server({**m.DEFAULT_SERVER, "name": "Work", "host": "ftp.example.com"},
                 old_name="Office")
    again = m.Settings(str(path))
    assert [x["name"] for x in again.servers] == ["Home", "Work"]
    assert again.get_server("Home")["port"] == 2121
    again.delete_server("Home")
    assert [x["name"] for x in m.Settings(str(path)).servers] == ["Work"]


def test_damaged_settings_file_is_ignored(tmp_path):
    path = tmp_path / "cfg.json"
    path.write_text("{not json")
    assert m.Settings(str(path)).servers == []


# --- worker against a real server ---------------------------------------

@pytest.fixture()
def server(tmp_path):
    env = os.environ.get("MSFTP_TEST_SERVER")
    if env:
        host, port, user, pw, root = env.split(":", 4)
        for name in os.listdir(root):
            p = os.path.join(root, name)
            if os.path.isfile(p):
                os.remove(p)
        yield {"host": host, "port": int(port), "user": user, "password": pw}, root
        return
    pyftpdlib = pytest.importorskip("pyftpdlib")  # noqa: F841
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import FTPServer
    root = tmp_path / "server"
    root.mkdir()
    auth = DummyAuthorizer()
    auth.add_user("alice", "secret", str(root), perm="elradfmwMT")
    handler = type("H", (FTPHandler,), {"authorizer": auth})
    srv = FTPServer(("127.0.0.1", 0), handler)
    port = srv.socket.getsockname()[1]
    t = threading.Thread(target=srv.serve_forever, kwargs={"timeout": 0.1}, daemon=True)
    t.start()
    yield {"host": "127.0.0.1", "port": port, "user": "alice", "password": "secret"}, str(root)
    srv.close_all()


class Collector:
    def __init__(self):
        self.q = queue.Queue()

    def __call__(self, kind, payload):
        self.q.put((kind, payload))

    def wait_for(self, kind, timeout=20, pred=lambda p: True):
        end = time.time() + timeout
        seen = []
        while time.time() < end:
            try:
                k, p = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            seen.append((k, p))
            if k == "error":
                raise AssertionError(p)
            if k == kind and pred(p):
                return p
        raise AssertionError(f"timed out waiting for {kind}; saw {[s[0] for s in seen]}")


def md5(path):
    with open(path, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()


def test_worker_upload_download_and_cancel(server, tmp_path):
    params, root = server
    local = tmp_path / "local"
    local.mkdir()
    big = local / "big.bin"
    big.write_bytes(os.urandom(3 * 1024 * 1024))
    events = Collector()
    w = m.FTPWorker(events)
    w.start()
    try:
        w.submit("connect", **params)
        events.wait_for("connected")
        events.wait_for("listing")

        w.submit("upload", transfer=True, local_path=str(big), remote_dir="/")
        listing = events.wait_for("listing", pred=lambda p: any(e.name == "big.bin" for e in p["entries"]))
        assert next(e for e in listing["entries"] if e.name == "big.bin").size == big.stat().st_size
        assert md5(os.path.join(root, "big.bin")) == md5(big)
        events.wait_for("queue", pred=lambda left: left == 0)

        out = tmp_path / "out"
        out.mkdir()
        w.submit("download", transfer=True, name="big.bin", is_dir=False,
                 local_dir=str(out), remote_dir="/")
        events.wait_for("queue", pred=lambda left: left == 0)
        assert md5(out / "big.bin") == md5(big)
        assert not (out / "big.bin.part").exists()

        # Cancelling with nothing running must not cancel the next transfer.
        w.cancel_transfers()
        (out / "big.bin").unlink()
        w.submit("download", transfer=True, name="big.bin", is_dir=False,
                 local_dir=str(out), remote_dir="/")
        events.wait_for("queue", pred=lambda left: left == 0)
        assert md5(out / "big.bin") == md5(big)
    finally:
        w.submit("quit")
        w.join(5)

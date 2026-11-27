"""End-to-end tests for FTPClient against a local pyftpdlib server."""
import os
import threading

import pytest

pyftpdlib = pytest.importorskip("pyftpdlib")
from pyftpdlib.authorizers import DummyAuthorizer  # noqa: E402
from pyftpdlib.handlers import FTPHandler  # noqa: E402
from pyftpdlib.servers import FTPServer  # noqa: E402

import ftpclient  # noqa: E402
from ftpclient import FTPClient, _parse_list_line  # noqa: E402


@pytest.fixture()
def server(tmp_path):
    root = tmp_path / "server"
    root.mkdir()
    (root / "hello.txt").write_text("hello world")
    auth = DummyAuthorizer()
    auth.add_user("alice", "secret", str(root), perm="elradfmwMT")
    handler = type("H", (FTPHandler,), {"authorizer": auth})
    srv = FTPServer(("127.0.0.1", 0), handler)
    port = srv.socket.getsockname()[1]
    t = threading.Thread(target=srv.serve_forever, kwargs={"timeout": 0.1}, daemon=True)
    t.start()
    yield port, root
    srv.close_all()


@pytest.fixture()
def client(server):
    port, _ = server
    c = FTPClient()
    c.connect("127.0.0.1", port, "alice", "secret")
    yield c
    c.close()


def test_ls_cd_mkdir(client, server):
    _, root = server
    assert [e.name for e in client.listdir()] == ["hello.txt"]
    client.mkdir("sub")
    names = [(e.name, e.is_dir) for e in client.listdir()]
    assert names[0] == ("sub", True)  # dirs sort first
    assert client.cd("sub") == "/sub"
    assert client.listdir() == []


def test_get_put_rm(client, server, tmp_path):
    _, root = server
    seen = []
    local = client.get("hello.txt", str(tmp_path / "dl.txt"), lambda d, t: seen.append((d, t)))
    assert open(local).read() == "hello world"
    assert seen[-1] == (11, 11)
    src = tmp_path / "up.bin"
    src.write_bytes(os.urandom(200_000))
    client.put(str(src), "up.bin")
    assert (root / "up.bin").read_bytes() == src.read_bytes()
    client.rm("up.bin")
    assert not (root / "up.bin").exists()


def test_rename_and_recursive_rmdir(client, server):
    _, root = server
    client.mkdir("a")
    client.mkdir("a/b")
    client.put(__file__, "a/b/f.py")
    client.rename("a", "c")
    client.rmdir("c", recursive=True)
    assert not (root / "c").exists()


def test_cli_one_shot(server, capsys):
    port, _ = server
    rc = ftpclient.main(["127.0.0.1", "-P", str(port), "-u", "alice", "-p", "secret", "ls"])
    assert rc == 0
    assert "hello.txt" in capsys.readouterr().out


def test_cli_save_then_use_saved_connection(server, capsys, tmp_path):
    from connections import SavedConnections
    from test_connections import FakeStore
    port, _ = server
    saved = SavedConnections(str(tmp_path / "settings.json"), FakeStore())
    rc = ftpclient.main(["-P", str(port), "-u", "alice", "-p", "secret",
                         "--save", "test", "--save-password", "127.0.0.1", "pwd"], saved)
    assert rc == 0
    assert "Saved as test (with password)" in capsys.readouterr().out
    rc = ftpclient.main(["test", "ls"], saved)  # no host, port, user or password typed
    assert rc == 0
    assert "hello.txt" in capsys.readouterr().out


def test_parse_list_lines():
    e = _parse_list_line("-rw-r--r--   1 ftp ftp   1234 Jan 01 10:00 my file.txt")
    assert (e.name, e.is_dir, e.size) == ("my file.txt", False, 1234)
    d = _parse_list_line("01-02-24  10:00AM       <DIR>          Folder")
    assert (d.name, d.is_dir) == ("Folder", True)

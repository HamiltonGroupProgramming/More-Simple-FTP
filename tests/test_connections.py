"""Saved connections: storage, passwords kept out of the settings file, and CLI use."""
import json

import pytest

from appsettings import load_settings, save_settings
from connections import Connection, SavedConnections, default_name


class FakeStore:
    available = True

    def __init__(self):
        self.data = {}

    def get(self, name):
        return self.data.get(name)

    def set(self, name, password):
        self.data[name] = password

    def delete(self, name):
        self.data.pop(name, None)


@pytest.fixture()
def saved(tmp_path):
    return SavedConnections(str(tmp_path / "settings.json"), FakeStore())


def test_save_list_and_forget(saved):
    assert saved.all() == []
    saved.save(Connection("work", "ftp.example.com", 2121, "alice", tls=True, save_password=True), "pw")
    saved.save(Connection("home", "192.168.1.5"))
    assert saved.names() == ["work", "home"]
    work = saved.get("WORK")
    assert (work.host, work.port, work.user, work.tls) == ("ftp.example.com", 2121, "alice", True)
    assert saved.password(work) == "pw"
    assert saved.password(saved.get("home")) is None
    assert saved.last_used().name == "home"
    assert saved.remove("work")
    assert saved.names() == ["home"]
    assert saved.store.data == {}
    assert not saved.remove("work")


def test_password_never_written_to_settings_file(saved):
    saved.save(Connection("work", "h", user="alice", save_password=True), "s3cret!")
    with open(saved.settings_file) as fh:
        assert "s3cret!" not in fh.read()


def test_resaving_without_password_removes_it(saved):
    saved.save(Connection("work", "h", user="alice", save_password=True), "pw")
    saved.save(Connection("work", "h2", user="alice"))
    assert saved.names() == ["work"]
    assert saved.get("work").host == "h2"
    assert saved.store.data == {}


def test_connections_survive_other_settings(saved):
    saved.save(Connection("work", "h"))
    settings = load_settings(saved.settings_file)
    settings["look"] = "midnight"
    save_settings(settings, saved.settings_file)
    assert saved.names() == ["work"]


def test_bad_entries_are_skipped(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"connections": [{"name": "ok", "host": "h"}, {"name": "x"},
                                                 "junk", {"name": "", "host": "h"},
                                                 {"name": "p", "host": "h", "port": "nope"}]}))
    assert SavedConnections(str(path), FakeStore()).names() == ["ok"]


def test_default_name():
    assert default_name("ftp.example.com", "anonymous") == "ftp.example.com"
    assert default_name("ftp.example.com", "alice") == "alice@ftp.example.com"


def test_cli_lists_and_forgets(saved, capsys):
    import ftpclient
    assert ftpclient.main(["--saved"], saved) == 0
    assert "No saved connections" in capsys.readouterr().out
    saved.save(Connection("work", "ftp.example.com", user="alice", save_password=True), "pw")
    assert ftpclient.main(["--saved"], saved) == 0
    assert "work: alice@ftp.example.com:21, password saved" in capsys.readouterr().out
    assert ftpclient.main(["--forget", "work"], saved) == 0
    assert ftpclient.main(["--forget", "work"], saved) == 1


def test_changelog_covers_current_version():
    import os
    import ftpclient
    path = os.path.join(os.path.dirname(__file__), os.pardir, "CHANGELOG.md")
    with open(path, encoding="utf-8") as fh:
        assert f"## {ftpclient.__version__} " in fh.read()

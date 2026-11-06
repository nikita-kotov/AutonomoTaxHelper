"""Where the data lives: it must stay out of the project folder (and out of git) by default."""
from pathlib import Path

import pytest

from taxhelper import db


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """A fake project folder and a fake per-user data folder, with no TAXHELPER_DATA set."""
    project, user = tmp_path / "project", tmp_path / "appdata" / "TaxHelper"
    project.mkdir()
    monkeypatch.delenv("TAXHELPER_DATA", raising=False)
    monkeypatch.setattr(db, "PROJECT_ROOT", project)
    monkeypatch.setattr(db, "user_data_dir", lambda: user)
    return project, user


def test_environment_variable_wins(sandbox, monkeypatch, tmp_path):
    project, _user = sandbox
    (project / "data").mkdir()
    (project / "data" / "taxhelper.db").write_bytes(b"x")
    monkeypatch.setenv("TAXHELPER_DATA", str(tmp_path / "elsewhere"))
    assert db.resolve_data_dir() == tmp_path / "elsewhere"


def test_new_installs_use_the_user_folder_not_the_project(sandbox):
    project, user = sandbox
    assert db.resolve_data_dir() == user
    folder = db.data_dir()
    assert folder == user and (folder / "documents").is_dir()
    assert project not in folder.parents and not (project / "data").exists()  # nothing is created in the project


def test_existing_project_data_keeps_working_and_is_shielded_from_git(sandbox):
    project, _user = sandbox
    (project / "data").mkdir()
    (project / "data" / "taxhelper.db").write_bytes(b"x")
    folder = db.data_dir()
    assert folder == project / "data"
    shield = folder / ".gitignore"
    assert shield.is_file() and shield.read_text(encoding="utf-8").splitlines()[-1] == "*"


def test_an_empty_project_data_folder_does_not_count(sandbox):
    project, user = sandbox
    (project / "data").mkdir()  # no database inside: a leftover, not an install
    assert db.resolve_data_dir() == user


def test_no_shield_is_written_outside_the_project(sandbox, tmp_path, monkeypatch):
    monkeypatch.setenv("TAXHELPER_DATA", str(tmp_path / "elsewhere"))
    assert not (db.data_dir() / ".gitignore").exists()


def test_the_real_default_is_outside_the_project_tree():
    default = db.user_data_dir()
    assert default.name == "TaxHelper"
    assert db.PROJECT_ROOT not in default.parents and default != db.PROJECT_ROOT


def test_the_project_gitignore_covers_data_and_backups():
    rules = (Path(db.PROJECT_ROOT) / ".gitignore").read_text(encoding="utf-8").split()
    assert {"data/", "*.db", "taxhelper-backup-*.zip", "*.pfx", "*.p12"} <= set(rules)

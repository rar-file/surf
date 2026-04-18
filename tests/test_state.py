from pathlib import Path

from core.state import get_data_dir, data_file


def test_get_data_dir_defaults_to_local_surf_folder(monkeypatch):
    monkeypatch.delenv("SURF_DATA_DIR", raising=False)
    path = get_data_dir()
    assert path.name == ".surf"


def test_get_data_dir_respects_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("SURF_DATA_DIR", str(tmp_path / "surf-data"))
    path = get_data_dir()
    assert path == (tmp_path / "surf-data").resolve()


def test_data_file_returns_path_inside_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SURF_DATA_DIR", str(tmp_path / "surf-data"))
    file_path = data_file("memory.json")
    assert file_path.parent == (tmp_path / "surf-data").resolve()
    assert file_path.name == "memory.json"

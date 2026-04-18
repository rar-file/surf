from pathlib import Path


def test_readme_mentions_runtime_state_folder():
    readme = Path("README.md").read_text(encoding="utf-8")
    assert ".surf/" in readme
    assert "SURF_DATA_DIR" in readme


def test_first_run_doc_exists():
    assert Path("docs/FIRST_RUN.md").exists()

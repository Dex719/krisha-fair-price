"""README acceptance checks for issue #82."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_readme_header_is_rebranded_to_bagam():
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "title: baǵam" in text
    assert 'alt="baǵam"' in text
    assert "**baǵam — справедливая цена квартиры в Алматы" in text
    assert "https://dex719-krisha-fair-price.hf.space" in text
    assert "FairPrice —" not in text
    assert 'alt="FairPrice"' not in text


def test_readme_documents_linux_lockfiles_and_local_dev_install():
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert (
        "`requirements*.lock` собраны под Linux (CI/Docker); "
        "локальная разработка на Windows/macOS — `pip install -e \".[dev]\"`"
    ) in text

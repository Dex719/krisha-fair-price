"""README acceptance checks for issue #82."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_readme_header_is_rebranded_to_bagam():
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert 'alt="baǵam"' in text
    assert "**baǵam — справедливая цена квартиры в Алматы" in text
    assert "https://dex719-krisha-fair-price.hf.space" in text
    assert "FairPrice —" not in text
    assert 'alt="FairPrice"' not in text


def test_readme_has_no_hf_front_matter_and_uses_vector_logo():
    """Метаданные HF Space добавляет deploy-hf.yml при пуше на Space —
    в репозиторном README их быть не должно (иначе на GitHub они
    рендерятся таблицей над лого)."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert not text.startswith("---")
    assert "title: baǵam" not in text
    assert "sdk: docker" not in text
    assert 'srcset="docs/logo-dark.svg"' in text
    assert 'src="docs/logo-light.svg"' in text


def test_readme_documents_linux_lockfiles_and_local_dev_install():
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert (
        "`requirements*.lock` собраны под Linux (CI/Docker); "
        "локальная разработка на Windows/macOS — `pip install -e \".[dev]\"`"
    ) in text


def test_readme_metrics_block_matches_model_meta():
    """README не имеет права врать про качество модели.

    Числа в блоке METRICS генерируются из models/model_meta.json скриптом
    scripts/sync_readme_metrics.py — тест проверяет, что блок на месте и
    сгенерирован из текущей меты (`--check` того же скрипта).
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "sync_readme_metrics", ROOT / "scripts" / "sync_readme_metrics.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert module.BEGIN in text and module.END in text
    assert "МАРКЕР" not in text


def test_readme_has_no_frozen_accuracy_badge():
    """Бейдж MAPE показывал 7.6% из меты полуторамесячной давности.

    Живой бейдж берёт число из /api/health, поэтому устареть не может.
    """
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "img.shields.io/badge/CatBoost-MAPE" not in text
    assert "img.shields.io/badge/dynamic/json" in text
    assert "tests-523_passed" not in text

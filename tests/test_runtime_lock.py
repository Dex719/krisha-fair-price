"""issue #119: прод-образ тащил matplotlib/plotly/graphviz/pillow/... —
plotting-хвост, который catboost объявляет обязательным в Requires-Dist,
хотя нужен только для plot_*/calc_feature_statistics, не для fit/predict.

Регрессионные тесты:
- requirements-runtime.lock (то, что реально ставит Dockerfile) не содержит
  ни одного пакета из этого хвоста;
- он не разъехался с requirements.lock (забыли перегенерировать после
  `make lock`);
- Dockerfile ставит именно requirements-runtime.lock, с флагом --no-deps
  (без него pip заново подтянет хвост из Requires-Dist самого catboost).
"""

from pathlib import Path

from scripts.gen_runtime_lock import PLOTTING_TAIL, filter_runtime_lock

ROOT = Path(__file__).resolve().parent.parent


def test_runtime_lock_excludes_plotting_tail():
    text = (ROOT / "requirements-runtime.lock").read_text(encoding="utf-8")
    for pkg in PLOTTING_TAIL:
        assert f"\n{pkg}==" not in text, f"{pkg} should not be in the runtime-only lock"
    # Реальные runtime-зависимости должны остаться на месте.
    for pkg in ("catboost", "numpy", "pandas", "scipy", "six", "fastapi", "uvicorn"):
        assert f"\n{pkg}==" in text, f"{pkg} missing from runtime lock"


def test_runtime_lock_matches_generator_output():
    source = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    committed = (ROOT / "requirements-runtime.lock").read_text(encoding="utf-8")
    assert filter_runtime_lock(source) == committed, (
        "requirements-runtime.lock не соответствует requirements.lock — "
        "перегенерируй через `make lock` (scripts/gen_runtime_lock.py)"
    )


def test_dockerfile_installs_runtime_lock_with_no_deps():
    lines = (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
    copy_lines = [line for line in lines if line.startswith("COPY") and "requirements" in line]
    install_lines = [
        line for line in lines if line.startswith("RUN pip install") and "requirements" in line
    ]
    assert copy_lines and all("requirements-runtime.lock" in line for line in copy_lines)
    assert install_lines and all(
        "--no-deps" in line and "requirements-runtime.lock" in line for line in install_lines
    )

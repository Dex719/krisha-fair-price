#!/usr/bin/env python
"""CLI: обучение модели. Результат — models/model.cbm + метрики в models/model_meta.json
(для аренды, --deal arenda, — то же в models/rent/)."""

import argparse
import logging

from krisha.model_spec import SPECS, spec_for
from krisha.train import train


def main() -> None:
    parser = argparse.ArgumentParser(description="Обучение CatBoost на данных из SQLite")
    parser.add_argument("--iterations", type=int, default=2000)
    parser.add_argument(
        "--deal", choices=sorted(SPECS), default="prodazha",
        help="какую модель учить: продажа (₸) или аренда (₸/мес)",
    )
    parser.add_argument(
        "--compare-old", default=None, metavar="PATH",
        help="Путь к прошлой model.cbm: оценить её на новом test-сплите "
             "(честное сравнение для метрического гейта)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    spec = spec_for(args.deal)
    metrics = train(iterations=args.iterations, old_model_path=args.compare_old, spec=spec)
    unit = "₸/мес" if spec.is_rent else "₸"
    print(f"\n=== Итог ({spec.label}) ===")
    print(f"Модель:   MAE {metrics['model']['mae']:,.0f} {unit} | MAPE {metrics['model']['mape']:.1%} | R² {metrics['model']['r2']:.3f}")
    print(f"Baseline: MAE {metrics['baseline']['mae']:,.0f} {unit} | MAPE {metrics['baseline']['mape']:.1%} | R² {metrics['baseline']['r2']:.3f}")


if __name__ == "__main__":
    main()

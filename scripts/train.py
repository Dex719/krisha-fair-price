#!/usr/bin/env python
"""CLI: обучение модели. Результат — models/model.cbm + метрики в models/model_meta.json."""

import argparse
import logging

from krisha.train import train


def main() -> None:
    parser = argparse.ArgumentParser(description="Обучение CatBoost на данных из SQLite")
    parser.add_argument("--iterations", type=int, default=2000)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    metrics = train(iterations=args.iterations)
    print("\n=== Итог ===")
    print(f"Модель:   MAE {metrics['model']['mae']:,.0f} ₸ | MAPE {metrics['model']['mape']:.1%} | R² {metrics['model']['r2']:.3f}")
    print(f"Baseline: MAE {metrics['baseline']['mae']:,.0f} ₸ | MAPE {metrics['baseline']['mape']:.1%} | R² {metrics['baseline']['r2']:.3f}")


if __name__ == "__main__":
    main()

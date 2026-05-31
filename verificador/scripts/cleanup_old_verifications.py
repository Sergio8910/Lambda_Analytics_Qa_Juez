#!/usr/bin/env python
"""CLI para purgar verifications viejas.

Uso:
    python -m verificador.scripts.cleanup_old_verifications --days 90 --dry-run
    python -m verificador.scripts.cleanup_old_verifications --days 30 --yes

Sin `--yes` y sin `--dry-run` pide confirmación interactiva antes de borrar.
"""
from __future__ import annotations

import argparse
import os
import sys


# Permite correr el script directamente (no solo `python -m ...`).
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cleanup_old_verifications",
        description="Borra verifications viejas según política de retention.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Días a retener (default: 90). Filas con created_at más antiguas se borran.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Solo cuenta filas afectadas, no borra nada.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="No pedir confirmación interactiva (modo no-interactivo / cron).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    from verificador.retention import purge_old_verifications

    if not args.dry_run and not args.yes:
        prompt = (
            f"Vas a borrar todas las verifications con created_at < hoy - {args.days} días.\n"
            "Confirmar borrado? [y/N] "
        )
        try:
            answer = input(prompt).strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes", "s", "si", "sí"):
            print("Cancelado.")
            return 1

    result = purge_old_verifications(days=args.days, dry_run=args.dry_run)
    label = "Borradas (dry-run)" if args.dry_run else "Borradas"
    print(f"{label}: {result['deleted_count']}")
    print(f"oldest_kept_at: {result['oldest_kept_at']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Entry point: `python -m crisp_recipes`."""
import logging
import sys

from crisp_recipes.pipeline import run


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> int:
    _configure_logging()
    try:
        run()
    except Exception:  # noqa: BLE001 - top-level guard, log and exit non-zero
        logging.getLogger("crisp_recipes").exception("Pipeline failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

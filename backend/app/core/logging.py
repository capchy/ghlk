import logging


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    # Request lines from httpx are noisy at INFO; our client logs what matters.
    logging.getLogger("httpx").setLevel(logging.WARNING)

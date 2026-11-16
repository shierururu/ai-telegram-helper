"""Technical schema check only; success does not imply prototype readiness."""

from alpha_course.entrypoints.common import execute, technical_check
from alpha_course.infrastructure.config import Settings


async def run(settings: Settings) -> None:
    await technical_check(settings, "check")


def main() -> int:
    return execute(run)


if __name__ == "__main__":
    raise SystemExit(main())

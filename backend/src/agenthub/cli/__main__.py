"""仅提供当前 Unit 已实现的显式 seed-demo 命令。"""

import argparse
import asyncio

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from agenthub.auth.security import PasswordSecurity
from agenthub.cli.seed import DemoSeedService
from agenthub.core.config import get_settings
from agenthub.core.errors import AppError
from agenthub.db.database import Database


async def seed_demo() -> str:
    settings = get_settings()
    database = Database(settings)
    try:
        return await DemoSeedService(
            settings, database, PasswordSecurity(settings.password_concurrency)
        ).run()
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="agenthub")
    parser.add_argument("command", choices=["seed-demo"])
    parser.parse_args()
    try:
        result = asyncio.run(seed_demo())
    except AppError as exc:
        parser.exit(1, f"{exc.code}: {exc.message}\n")
    except ValidationError:
        parser.exit(1, "configuration_error: Invalid application configuration\n")
    except (SQLAlchemyError, OSError, TimeoutError):
        parser.exit(1, "dependency_unavailable: Seed could not be completed\n")
    print("demo_seed:", result)


if __name__ == "__main__":
    main()

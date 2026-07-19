import asyncio
import selectors
import sys

from app.core.config import get_settings
from app.workflows.alert.checkpoint import setup_checkpoint_database


async def main() -> None:
    await setup_checkpoint_database(get_settings().database_url)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(
            main(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    else:
        asyncio.run(main())

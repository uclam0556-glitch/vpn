import asyncio
import logging

from .config import get_settings
from .db import SessionFactory, create_schema
from .remnawave import RemnawaveError, make_remnawave_gateway
from .services import check_due_subscription_health, expire_due_subscriptions

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if settings.auto_create_schema:
        await create_schema()
    gateway = make_remnawave_gateway(settings)
    while True:
        try:
            async with SessionFactory() as session:
                count = await expire_due_subscriptions(session, gateway)
                if count:
                    logger.info("Expired %s subscriptions", count)
                checked = await check_due_subscription_health(session, settings)
                if checked:
                    logger.info("Checked %s subscription health records", checked)
        except RemnawaveError:
            logger.exception("Maintenance could not reach Remnawave")
        except Exception:
            logger.exception("Maintenance loop failed")
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())

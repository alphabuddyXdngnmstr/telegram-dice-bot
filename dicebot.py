import re
import math
import html
import asyncio
from pathlib import Path
from typing import Optional, Tuple, List, Dict

@@ -1911,32 +1912,33 @@ def main():
        raise RuntimeError("TELEGRAM_BOT_TOKEN oder BASE_URL fehlt")

    base_url = base_url.rstrip("/")
    app = Application.builder().token(token).build()

    ptb_app = Application.builder().token(token).updater(None).build()

    init_encounters()
    init_magic_tables()

    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("start", help_cmd))
    ptb_app.add_handler(CommandHandler("help", help_cmd))
    ptb_app.add_handler(CommandHandler("start", help_cmd))

    app.add_handler(CommandHandler("roll", roll))
    app.add_handler(CommandHandler("rollchance", rollchance))
    ptb_app.add_handler(CommandHandler("roll", roll))
    ptb_app.add_handler(CommandHandler("rollchance", rollchance))

    app.add_handler(CommandHandler("rollhunt", rollhunt))
    app.add_handler(CallbackQueryHandler(rollhunt_pick_mod, pattern=r"^hunt_mod:"))
    app.add_handler(CallbackQueryHandler(rollhunt_cancel_cb, pattern=r"^hunt_cancel$"))
    ptb_app.add_handler(CommandHandler("rollhunt", rollhunt))
    ptb_app.add_handler(CallbackQueryHandler(rollhunt_pick_mod, pattern=r"^hunt_mod:"))
    ptb_app.add_handler(CallbackQueryHandler(rollhunt_cancel_cb, pattern=r"^hunt_cancel$"))

    app.add_handler(CommandHandler("rollwaldkarte", rollwaldkarte))
    app.add_handler(CallbackQueryHandler(rollwaldkarte_pick_level, pattern=r"^waldkarte_level:"))
    ptb_app.add_handler(CommandHandler("rollwaldkarte", rollwaldkarte))
    ptb_app.add_handler(CallbackQueryHandler(rollwaldkarte_pick_level, pattern=r"^waldkarte_level:"))

    app.add_handler(CommandHandler("rollplayerbehaviour", rollplayerbehaviour))
    ptb_app.add_handler(CommandHandler("rollplayerbehaviour", rollplayerbehaviour))

    app.add_handler(CommandHandler("setbiom", setbiom))
    app.add_handler(CommandHandler("biom", biom))
    app.add_handler(CommandHandler("rollbiom", rollbiom))
    app.add_handler(CallbackQueryHandler(setbiom_pick, pattern=r"^biom_set:"))
    ptb_app.add_handler(CommandHandler("setbiom", setbiom))
    ptb_app.add_handler(CommandHandler("biom", biom))
    ptb_app.add_handler(CommandHandler("rollbiom", rollbiom))
    ptb_app.add_handler(CallbackQueryHandler(setbiom_pick, pattern=r"^biom_set:"))

    app.add_handler(CommandHandler("encdebug", encdebug))
    ptb_app.add_handler(CommandHandler("encdebug", encdebug))

    encounter_conv = ConversationHandler(
        entry_points=[CommandHandler("rollencounter", rollencounter_start)],
@@ -1948,7 +1950,7 @@ def main():
        fallbacks=[CommandHandler("cancel", rollencounter_cancel)],
        allow_reentry=True,
    )
    app.add_handler(encounter_conv)
    ptb_app.add_handler(encounter_conv)

    oracle_conv = ConversationHandler(
        entry_points=[CommandHandler("rolloracle", rolloracle_start)],
@@ -1960,7 +1962,7 @@ def main():
        fallbacks=[CommandHandler("cancel", rolloracle_cancel)],
        allow_reentry=True,
    )
    app.add_handler(oracle_conv)
    ptb_app.add_handler(oracle_conv)

    treasure_conv = ConversationHandler(
        entry_points=[CommandHandler("rollschatz", rollschatz_start)],
@@ -1977,7 +1979,7 @@ def main():
        fallbacks=[CommandHandler("cancel", rollschatz_cancel)],
        allow_reentry=True,
    )
    app.add_handler(treasure_conv)
    ptb_app.add_handler(treasure_conv)

    dungeon_conv = ConversationHandler(
        entry_points=[CommandHandler("rolldungeon", rolldungeon_start)],
@@ -1994,20 +1996,40 @@ def main():
        fallbacks=[CommandHandler("cancel", rolldungeon_cancel_cmd)],
        allow_reentry=True,
    )
    app.add_handler(dungeon_conv)

    web_app = web.Application()
    web_app.router.add_get("/", ping)
    web_app.router.add_get("/webhook", ping)

    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path="webhook",
        webhook_url=f"{base_url}/webhook",
        drop_pending_updates=True,
        web_app=web_app,
    )
    ptb_app.add_handler(dungeon_conv)

    async def health(_request: web.Request) -> web.Response:
        return web.Response(text="ok", content_type="text/plain")

    async def telegram_webhook(request: web.Request) -> web.Response:
        data = await request.json()
        await ptb_app.update_queue.put(Update.de_json(data=data, bot=ptb_app.bot))
        return web.Response(text="ok", content_type="text/plain")

    aio_app = web.Application()
    aio_app.router.add_get("/", health)
    aio_app.router.add_get("/health", health)
    aio_app.router.add_post("/webhook", telegram_webhook)

    async def on_startup(_app: web.Application) -> None:
        await ptb_app.initialize()
        await ptb_app.start()
        await ptb_app.bot.set_webhook(
            url=f"{base_url}/webhook",
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
        )

    async def on_cleanup(_app: web.Application) -> None:
        await ptb_app.bot.delete_webhook(drop_pending_updates=True)
        await ptb_app.stop()
        await ptb_app.shutdown()

    aio_app.on_startup.append(on_startup)
    aio_app.on_cleanup.append(on_cleanup)

    web.run_app(aio_app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()

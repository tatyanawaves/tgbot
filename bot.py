import os
import asyncio
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

import config
import db
from scraper import get_latest_news
from llm import synthesize_digest

logging.basicConfig(level=logging.INFO)

dp = Dispatcher()
run_lock = asyncio.Lock()


async def _send_message_safe(bot: Bot, chat_id, text: str) -> bool:
    """Send with HTML parsing, falling back to plain text if the LLM output
    contains characters Telegram can't parse as HTML entities (e.g. a bare
    '<' or '&'). Returns True only on an actual successful send."""
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)
        return True
    except TelegramBadRequest as e:
        if "can't parse entities" not in str(e).lower():
            logging.error(f"Telegram rejected message to {chat_id}: {e}")
            return False
        logging.warning(f"HTML parse failed for {chat_id}, retrying as plain text: {e}")
    except Exception as e:
        logging.error(f"Error sending message to {chat_id}: {e}")
        return False

    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=None)
        return True
    except Exception as e:
        logging.error(f"Plain-text retry to {chat_id} also failed: {e}")
        return False


async def process_articles(bot: Bot, publisher_bot: Bot = None, limit=None) -> bool:
    """Fetch new articles, synthesize into digest, publish to channel and notify admin."""
    try:
        logging.info("Checking for new articles...")
        all_news = await asyncio.to_thread(
            get_latest_news, config.RSS_FEEDS, config.TELEGRAM_CHANNELS, limit
        )

        # Filter out already processed and too-short articles
        new_articles = []
        for news in all_news:
            if db.is_processed(news['id']):
                continue
            if not news['text'] or len(news['text']) < 100:
                logging.warning(f"Skipping {news['url']} - text too short")
                db.mark_processed(news['id'])
                continue
            new_articles.append(news)

        if not new_articles:
            logging.info("No new articles found.")
            return False

        logging.info(f"Found {len(new_articles)} new articles. Synthesizing digest...")

        # Synthesize all new articles into one digest
        digest = await asyncio.to_thread(synthesize_digest, new_articles)

        if not digest:
            logging.warning("Digest synthesis failed. Will retry next cycle.")
            return False

        # Build the message (без заголовка — LLM сам делает хук)
        clean = digest.replace('*', '').replace('_', '').replace('#', '')
        if len(clean) > 4000:
            clean = clean[:4000] + "\n\n..."

        sources_count = len(new_articles)
        sent_ok = False

        # 1) Публикуем в канал через Виктора
        if publisher_bot and config.CHANNEL_ID:
            sent_ok = await _send_message_safe(publisher_bot, config.CHANNEL_ID, clean)
            if sent_ok:
                logging.info(f"Published to {config.CHANNEL_ID} via Viktor ({len(clean)} chars, {sources_count} articles)")

        # 2) Копия админу (всегда)
        admin_id = config.ADMIN_ID
        admin_ok = False
        if admin_id and admin_id != "YOUR_TELEGRAM_ID":
            now = datetime.now().strftime("%H:%M")
            status = "✅ Опубликовано в канале" if sent_ok else "⚠️ Ошибка публикации в канале"
            admin_msg = f"📡 <b>FINCASH</b> · {now} · {sources_count} ист. · {status}\n\n{clean}"
            if len(admin_msg) > 4000:
                admin_msg = admin_msg[:4000] + "\n\n..."
            admin_ok = await _send_message_safe(bot, admin_id, admin_msg)

        if sent_ok or admin_ok:
            for art in new_articles:
                db.mark_processed(art['id'])
            logging.info(f"Digest sent successfully ({len(clean)} chars, {sources_count} articles)")
            return True

        logging.error("Both channel and admin delivery failed — leaving articles unprocessed for retry.")
        return False

    except Exception as e:
        logging.error(f"Error in process_articles: {e}")
        return False


async def check_for_news_loop(bot: Bot, publisher_bot: Bot = None):
    """Periodic background task to check for news."""
    cycle = 0
    while True:
        cycle += 1
        logging.info(f"=== Background cycle #{cycle} starting ===")
        try:
            limit = 3 if cycle == 1 else None
            await trigger_processing(bot, publisher_bot, limit=limit)
        except Exception as e:
            logging.error(f"Error in loop: {e}")

        logging.info(f"=== Cycle #{cycle} done. Sleeping {config.CHECK_INTERVAL_SECONDS}s ===")
        await asyncio.sleep(config.CHECK_INTERVAL_SECONDS)


async def trigger_processing(bot: Bot, publisher_bot: Bot = None, limit=None) -> bool:
    """Run one processing cycle while preventing overlapping launches."""
    if run_lock.locked():
        logging.info("Skipping trigger: processing is already running.")
        return False

    async with run_lock:
        return await process_articles(bot, publisher_bot, limit=limit)


@dp.message(CommandStart())
async def command_start_handler(message: types.Message):
    await message.answer(
        "Привет! Я <b>FINCASH BOT</b> 📊\n\n"
        "Я собираю финансовые новости из 10+ источников, "
        "синтезирую их в авторский дайджест и присылаю каждые 15 минут.\n\n"
        "Команды:\n"
        "/now — получить дайджест прямо сейчас\n"
        "/status — статус бота",
        parse_mode=ParseMode.HTML
    )


@dp.message(Command("now"))
async def command_now_handler(message: types.Message, bot: Bot):
    if str(message.from_user.id) != str(config.ADMIN_ID):
        await message.answer("Эта команда доступна только администратору.")
        return

    msg = await message.answer("🔄 Собираю новости и готовлю дайджест...")

    publisher_bot = None
    if config.PUBLISHER_BOT_TOKEN:
        from aiogram.client.default import DefaultBotProperties
        publisher_bot = Bot(
            token=config.PUBLISHER_BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )

    found = await trigger_processing(bot, publisher_bot, limit=None)

    if found:
        await msg.edit_text(f"✅ Дайджест опубликован в {config.CHANNEL_ID}!")
    else:
        await msg.edit_text("🤷 Новых новостей пока нет или синтез не удался.")


@dp.message(Command("status"))
async def command_status_handler(message: types.Message):
    await message.answer(
        f"📡 Бот работает\n"
        f"⏱ Интервал проверки: {config.CHECK_INTERVAL_SECONDS // 60} мин\n"
        f"📰 RSS-источников: {len(config.RSS_FEEDS)}\n"
        f"💬 TG-каналов: {len(config.TELEGRAM_CHANNELS)}"
    )


async def main():
    db.init_db()

    if not config.BOT_TOKEN or config.BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        logging.error("BOT_TOKEN is not set in config!")
        return

    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    # Бот-публикатор (Виктор) для постинга в канал
    publisher_bot = None
    if config.PUBLISHER_BOT_TOKEN:
        publisher_bot = Bot(
            token=config.PUBLISHER_BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        logging.info(f"Publisher bot (Viktor) initialized → channel {config.CHANNEL_ID}")

    logging.info("Bot starting...")

    # Запускаем "фейковый" веб-сервер для Render
    from aiohttp import web
    async def handle_ping(request):
        return web.Response(text="Bot is alive!")

    async def handle_run(request):
        if config.CRON_SECRET:
            provided = request.headers.get("X-Cron-Secret") or request.query.get("secret", "")
            if provided != config.CRON_SECRET:
                return web.Response(status=403, text="Forbidden")

        # Отвечаем сразу и запускаем обработку в фоне — cron-job.org и
        # другие внешние пинги обрывают запрос по таймауту (обычно 30с),
        # если ждать здесь завершения полного цикла (скрапинг + LLM).
        if run_lock.locked():
            return web.json_response({"ok": True, "status": "already_running"})

        asyncio.create_task(trigger_processing(bot, publisher_bot, limit=None))
        return web.json_response({"ok": True, "status": "started"})
    
    app = web.Application()
    app.router.add_get('/', handle_ping)
    app.router.add_get('/run', handle_run)
    app.router.add_post('/run', handle_run)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"Dummy web server started on port {port}")

    if config.RUN_VIA_HTTP_CRON:
        logging.info("HTTP cron mode enabled: waiting for /run requests")
    else:
        asyncio.create_task(check_for_news_loop(bot, publisher_bot))
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())

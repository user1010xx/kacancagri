import asyncio
import logging
import os
from datetime import date, datetime as dtm
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    TypeHandler,
    filters,
)

from config_store import ConfigStore
from excel_export import export_missed_calls_excel, sort_calls
from invekto_client import (
    InvektoError,
    call_key,
    fetch_missed_calls,
    format_call_message,
    get_available_queues,
    parse_command_dates,
)
from sent_store import SentStore

BASE_DIR = Path(__file__).resolve().parent
# DATA_DIR: Railway volume için /app/data kullanılabilir.
# Varsayılan: ./data (yerel geliştirme için)
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).resolve()

load_dotenv(BASE_DIR / ".env")

LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "bot.log"

# Console + rotating file (5MB x 5 backups)
log_formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Console
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
root_logger.addHandler(console_handler)

# Rotating file
file_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
)
file_handler.setFormatter(log_formatter)
root_logger.addHandler(file_handler)

logger = logging.getLogger(__name__)

config = ConfigStore(DATA_DIR / "config.json")
sent_store = SentStore(DATA_DIR / "sent_calls.json")

HELP_TEXT = (
    "Merhaba! Bu bot Invekto kaçan çağrıları Telegram'a iletir.\n\n"
    "Komutlar:\n"
    "/ayar - Mevcut ayarları göster\n"
    "/firmakodu <kod> - 8 haneli Invekto firma kodunu ayarla\n"
    "/chatid - Bu grubun ID'sini göster\n"
    "/ping - Bot bağlantı testi\n"
    "/stats - Bot istatistikleri (dedup kaydı, vs.)\n"
    "/kuyruklar - Invekto'daki departman/kuyruk adlarını listele\n"
    "/kacancagri <başlangıç>, <bitiş> - Tarih aralığındaki kaçan çağrıları Excel olarak gönder\n"
    "Örnek: /kacancagri 15.06.2026, 25.06.2026"
)


def _require_company_code() -> str | None:
    return config.company_code or None


def _allowed_chat_filter() -> filters.MessageFilter:
    class AllowedGroupFilter(filters.MessageFilter):
        def filter(self, message) -> bool:
            if message.chat.type not in ("group", "supergroup"):
                return False
            if message.chat_id != config.target_chat_id:
                logger.warning(
                    "Yetkisiz grup komutu reddedildi. gelen=%s beklenen=%s",
                    message.chat_id,
                    config.target_chat_id,
                )
                return False
            return True

    return AllowedGroupFilter()


async def log_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat:
        return
    text = update.effective_message.text if update.effective_message else "-"
    logger.info(
        "Gelen update: chat_id=%s chat_type=%s text=%s",
        chat.id,
        chat.type,
        text,
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT)


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    allowed = chat.id == config.target_chat_id
    await update.message.reply_text(
        f"pong\nchat_id={chat.id}\nbeklenen={config.target_chat_id}\n"
        f"yetkili_grup={'evet' if allowed else 'hayir'}"
    )


async def ayar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(config.as_text())


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    company_code = _require_company_code()
    total_tracked = sent_store.count()

    # Simple runtime stats from bot_data if available
    last_poll_count = context.bot_data.get("last_poll_count", "-") if context.bot_data else "-"
    last_poll_time = context.bot_data.get("last_poll_time", "-") if context.bot_data else "-"

    text = (
        "📊 Bot İstatistikleri\n\n"
        f"🏢 Firma Kodu: {company_code or 'Ayarlanmadı'}\n"
        f"🏷️ Departman: {config.department_name or 'Tümü'}\n"
        f"📦 Takip edilen (dedup) çağrı sayısı: {total_tracked}\n"
        f"⏱️ Polling aralığı: {config.polling_interval_seconds} sn\n"
        f"🕒 Son poll sonucu (bu oturum): {last_poll_count}\n"
        f"🕒 Son poll zamanı: {last_poll_time}\n"
    )
    await update.message.reply_text(text)


async def chatid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    allowed = chat.id == config.target_chat_id
    await update.message.reply_text(
        f"Sohbet ID: {chat.id}\n"
        f"Railway TELEGRAM_GROUP_CHAT_ID: {config.target_chat_id}\n"
        f"Durum: {'Bu grup yetkili' if allowed else 'Bu grup yetkili degil'}"
    )


async def firmakodu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Kullanım: /firmakodu 12345678")
        return

    code = context.args[0].strip()
    if not code.isdigit() or len(code) != 8:
        await update.message.reply_text("Firma kodu 8 haneli sayı olmalıdır.")
        return

    config.company_code = code
    await update.message.reply_text(f"✅ Firma kodu ayarlandı: {code}")


async def kuyruklar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    company_code = _require_company_code()
    if not company_code:
        await update.message.reply_text("Önce /firmakodu komutu ile firma kodunu ayarlayın.")
        return

    today = date.today()
    try:
        queues = await asyncio.to_thread(
            get_available_queues,
            company_code,
            today,
            today,
        )
    except InvektoError as exc:
        await update.message.reply_text(f"Invekto hatası: {exc}")
        return
    except Exception as exc:
        logger.exception("Kuyruk listesi alınamadı")
        await update.message.reply_text(f"Kuyruk listesi alınamadı: {exc}")
        return

    if not queues:
        await update.message.reply_text("Invekto'dan kuyruk listesi alınamadı.")
        return

    lines = ["📋 Invekto Kuyruk/Departman Adları\n"]
    for name, number in queues:
        if number:
            lines.append(f"• {name} (no: {number})")
        else:
            lines.append(f"• {name}")

    await update.message.reply_text("\n".join(lines))


async def kacancagri_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    company_code = _require_company_code()
    if not company_code:
        await update.message.reply_text("Önce /firmakodu komutu ile firma kodunu ayarlayın.")
        return

    if not context.args:
        await update.message.reply_text("Kullanım: /kacancagri 15.06.2026, 25.06.2026")
        return

    raw_dates = " ".join(context.args)
    try:
        start_date, end_date = parse_command_dates(raw_dates)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return

    await update.message.reply_text("Kaçan çağrılar sorgulanıyor, lütfen bekleyin...")

    try:
        calls = await asyncio.to_thread(
            fetch_missed_calls,
            company_code,
            start_date,
            end_date,
            department_name=config.department_name or None,
            uncompleted_only=False,
        )
    except InvektoError as exc:
        await update.message.reply_text(f"Invekto hatası: {exc}")
        return
    except Exception as exc:
        logger.exception("Kaçan çağrı sorgusu başarısız")
        await update.message.reply_text(f"Sorgu sırasında hata oluştu: {exc}")
        return

    if not calls:
        message = "Belirtilen aralıkta kaçan çağrı bulunamadı."
        if config.department_name:
            try:
                queues = await asyncio.to_thread(
                    get_available_queues,
                    company_code,
                    start_date,
                    end_date,
                )
                if queues:
                    names = ", ".join(name for name, _ in queues[:8])
                    message += (
                        f"\n\n⚠️ Ayarlı departman: {config.department_name}\n"
                        f"Invekto'daki kuyruk adları: {names}\n\n"
                        "Doğru adı görmek için /kuyruklar komutunu kullanın."
                    )
            except Exception:
                pass
        await update.message.reply_text(message)
        return

    calls = sort_calls(calls)
    filename = (
        f"kacancagri_{start_date.strftime('%d.%m.%Y')}_"
        f"{end_date.strftime('%d.%m.%Y')}.xlsx"
    )
    export_path = DATA_DIR / "exports" / filename

    try:
        await asyncio.to_thread(export_missed_calls_excel, calls, export_path)
        await update.message.reply_text(
            f"📋 Kaçan Çağrılar ({start_date.strftime('%d.%m.%Y')} - "
            f"{end_date.strftime('%d.%m.%Y')})\n"
            f"Toplam: {len(calls)}\n"
            "Excel dosyası hazırlanıyor..."
        )
        with export_path.open("rb") as excel_file:
            await update.message.reply_document(
                document=excel_file,
                filename=filename,
                caption=f"Toplam {len(calls)} kaçan çağrı",
            )
    except Exception as exc:
        logger.exception("Excel oluşturulamadı")
        await update.message.reply_text(f"Excel dosyası oluşturulamadı: {exc}")
    finally:
        if export_path.exists():
            export_path.unlink()


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Beklenmeyen hata: %s", context.error)


async def _seed_today_sent_calls() -> int:
    company_code = _require_company_code()
    if not company_code:
        return 0

    today = date.today()
    calls = await asyncio.to_thread(
        fetch_missed_calls,
        company_code,
        today,
        today,
        department_name=config.department_name or None,
        uncompleted_only=False,
    )
    keys = [call_key(call) for call in calls]
    sent_store.add_many(keys)
    return len(keys)


async def poll_missed_calls(context: ContextTypes.DEFAULT_TYPE) -> None:
    company_code = _require_company_code()
    if not company_code:
        return

    today = date.today()
    sent_now = 0

    try:
        calls = await asyncio.to_thread(
            fetch_missed_calls,
            company_code,
            today,
            today,
            department_name=config.department_name or None,
            uncompleted_only=config.notify_uncompleted_only,
        )
    except Exception as exc:
        logger.warning("Anlık kaçan çağrı kontrolü başarısız: %s", exc)
        return

    for call in calls:
        key = call_key(call)
        if sent_store.has(key):
            continue
        try:
            await context.bot.send_message(
                chat_id=config.target_chat_id,
                text=format_call_message(call),
            )
            sent_store.add(key)
            sent_now += 1
        except Exception as exc:
            logger.warning("Telegram bildirimi gönderilemedi: %s", exc)

    # Store lightweight stats for /stats
    if context.bot_data is not None:
        context.bot_data["last_poll_count"] = sent_now
        context.bot_data["last_poll_time"] = dtm.now().strftime("%d.%m.%Y %H:%M:%S")


async def post_init(application: Application) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    await application.bot.delete_webhook(drop_pending_updates=True)
    me = await application.bot.get_me()
    logger.info("Bot aktif: @%s", me.username)
    logger.info("Yetkili grup ID: %s", config.target_chat_id)

    try:
        seeded = await _seed_today_sent_calls()
        logger.info("Başlangıçta %s mevcut kaçan çağrı işaretlendi.", seeded)
    except Exception as exc:
        logger.warning("Başlangıç seed işlemi başarısız: %s", exc)


def main() -> None:
    missing = config.validate()
    if missing:
        raise SystemExit(f"Eksik veya hatalı ortam değişkenleri: {', '.join(missing)}")

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    allowed = _allowed_chat_filter()
    group_only = filters.ChatType.GROUPS

    application = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .build()
    )

    application.add_handler(TypeHandler(Update, log_update), group=-1)
    application.add_handler(CommandHandler("ping", ping_command, filters=group_only))
    application.add_handler(CommandHandler("chatid", chatid_command, filters=group_only))
    application.add_handler(CommandHandler("start", start_command, filters=allowed))
    application.add_handler(CommandHandler("help", start_command, filters=allowed))
    application.add_handler(CommandHandler("ayar", ayar_command, filters=allowed))
    application.add_handler(CommandHandler("stats", stats_command, filters=allowed))
    application.add_handler(CommandHandler("firmakodu", firmakodu_command, filters=allowed))
    application.add_handler(CommandHandler("kuyruklar", kuyruklar_command, filters=allowed))
    application.add_handler(CommandHandler("kacancagri", kacancagri_command, filters=allowed))
    application.add_error_handler(error_handler)

    application.job_queue.run_repeating(
        poll_missed_calls,
        interval=config.polling_interval_seconds,
        first=5,
        name="missed-call-poller",
    )

    logger.info("Polling başlıyor...")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    main()
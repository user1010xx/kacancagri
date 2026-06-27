import asyncio
import logging
import os
import time
from datetime import date, datetime as dtm, time as dt_time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from config_store import ConfigStore
from excel_export import export_missed_calls_excel, sort_calls
from personnel_store import PersonnelStore
from invekto_client import (
    InvektoError,
    build_phone_dahili_cache,
    call_key,
    fetch_missed_calls,
    get_available_queues,
    parse_command_dates,
)
from notifications import (
    build_missed_call_context,
    counts_as_failed_dm,
    deliver_missed_call_notification,
    should_mark_complete,
)
from sent_store import SentStore

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).resolve()

load_dotenv(BASE_DIR / ".env")

LOG_DIR = DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "bot.log"
_LOG_CONFIGURED = False


def _configure_logging() -> None:
    global _LOG_CONFIGURED
    if _LOG_CONFIGURED:
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    root_logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(log_formatter)
    root_logger.addHandler(file_handler)
    _LOG_CONFIGURED = True


_configure_logging()
logger = logging.getLogger(__name__)

config = ConfigStore(DATA_DIR / "config.json")
sent_store = SentStore(DATA_DIR / "sent_calls.json")
personnel_store = PersonnelStore(DATA_DIR / "personnels.json")

HELP_TEXT = (
    "Merhaba! Bu bot Invekto kaçan çağrıları Telegram'a iletir.\n\n"
    "Komutlar:\n"
    "/ayar - Mevcut ayarları göster\n"
    "/firmakodu <kod> - 8 haneli Invekto firma kodunu ayarla\n"
    "/chatid - Bu grubun ID'sini göster\n"
    "/ping - Bot bağlantı testi\n"
    "/stats - Bot istatistikleri (dedup kaydı, vs.)\n"
    "/temizle - Eski dedup kayıtlarını temizle\n"
    "/kuyruklar - Invekto'daki departman/kuyruk adlarını listele\n"
    "/kacancagri <başlangıç>, <bitiş> - Tarih aralığındaki kaçan çağrıları Excel olarak gönder\n"
    "/personelekle <dahili> <ad> <@kullanici> - Personel ekle/güncelle\n"
    "/personelsil <dahili> - Personeli sil\n"
    "/personeller - Kayıtlı personelleri listele\n"
    "Excel ile personel yüklemek için .xlsx dosyası gönderin (3 sütun: dahili, ad, @username)\n"
    "Personeller özel mesaj alabilmek için bota DM'den /start yazmalıdır.\n"
    "Örnek: /kacancagri 15.06.2026, 25.06.2026"
)

def _require_company_code() -> str | None:
    return config.company_code or None


def _fetch_kwargs() -> dict:
    return {
        "department_name": config.department_name or None,
        "loose_department_match": config.department_loose_match,
    }


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


def _update_bot_data(context: ContextTypes.DEFAULT_TYPE, **fields) -> None:
    if context.bot_data is None:
        return
    context.bot_data.update(fields)


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


async def private_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not user.username:
        await update.message.reply_text(
            "Telegram kullanıcı adınız (username) tanımlı olmalı. "
            "Ayarlar > Kullanıcı adı bölümünden ekleyin."
        )
        return

    linked = personnel_store.link_chat_id_by_username(user.username, update.effective_chat.id)
    if linked:
        await update.message.reply_text(
            "✅ Bağlantı kuruldu\n"
            "Artık kaçan çağrılar sizlere iletilecektir."
        )
    else:
        await update.message.reply_text(
            f"⚠️ @{user.username} için personel kaydı bulunamadı."
        )


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
    bot_data = context.bot_data or {}

    text = (
        "📊 Bot İstatistikleri\n\n"
        f"🏢 Firma Kodu: {company_code or 'Ayarlanmadı'}\n"
        f"🏷️ Departman: {config.department_name or 'Tümü'}\n"
        f"📦 Tamamlanan (dedup) çağrı: {sent_store.count()}\n"
        f"⏳ DM bekleyen (grup gönderildi): {sent_store.group_notified_count()}\n"
        f"👥 Kayıtlı personel: {personnel_store.count()}\n"
        f"⏱️ Polling aralığı: {config.polling_interval_seconds} sn\n"
        f"📨 Bildirim filtresi: {'Sadece tamamlanmamış' if config.notify_uncompleted_only else 'Tümü'}\n"
        f"🕒 Son poll (bu oturum): {bot_data.get('last_poll_count', '-')}\n"
        f"🕒 Son poll zamanı: {bot_data.get('last_poll_time', '-')}\n"
        f"⚡ Son API süresi: {bot_data.get('last_api_duration_ms', '-')} ms\n"
        f"❌ Son poll hatası: {bot_data.get('last_poll_error', '-')}\n"
        f"📭 Başarısız DM (bu oturum): {bot_data.get('failed_dm_count', 0)}\n"
    )
    await update.message.reply_text(text)


async def temizle_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    removed = sent_store.purge_old()
    await update.message.reply_text(f"✅ {removed} eski dedup kaydı temizlendi.")


async def personelekle_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 3:
        await update.message.reply_text(
            "Kullanım: /personelekle 105 \"Ahmet Yılmaz\" @ahmet_yilmaz\n"
            "veya /personelekle 105 Ahmet @ahmet_yilmaz"
        )
        return

    dahili = context.args[0].strip()
    username = context.args[-1].strip()
    ad = " ".join(context.args[1:-1]).strip().strip('"').strip("'")

    if not dahili or not ad:
        await update.message.reply_text("Dahili ve personel adı boş olamaz.")
        return

    if personnel_store.add_or_update(dahili, ad, username):
        await update.message.reply_text(
            f"✅ Personel eklendi/güncellendi: {ad} (Dahili: {dahili})\n"
            "Personel bota DM'den /start yazarak özel mesaj almayı aktifleştirmeli."
        )
    else:
        await update.message.reply_text("Personel eklenemedi.")


async def personelsil_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Kullanım: /personelsil 105")
        return

    dahili = context.args[0].strip()
    if personnel_store.remove(dahili):
        await update.message.reply_text(f"✅ Personel silindi: {dahili}")
    else:
        await update.message.reply_text("Böyle bir personel bulunamadı.")


async def personeller_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    items = personnel_store.get_all()
    if not items:
        await update.message.reply_text("Kayıtlı personel yok. Excel veya /personelekle ile ekleyin.")
        return

    lines = ["📋 Kayıtlı Personeller\n"]
    for p in items:
        dm = "✅ DM hazır" if p["dm_ready"] else "⚠️ /start bekliyor"
        lines.append(
            f"• {p['dahili_ad']} - {p['personel_adi']} - @{p['telegram_username']} ({dm})"
        )
    await update.message.reply_text("\n".join(lines))


async def personel_excel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    doc = update.message.document
    if not doc or not doc.file_name.lower().endswith(".xlsx"):
        return

    file = await context.bot.get_file(doc.file_id)
    temp_path = DATA_DIR / "temp_personel_upload.xlsx"
    await file.download_to_drive(temp_path)

    try:
        count = personnel_store.load_from_excel(temp_path)
        await update.message.reply_text(
            f"✅ Personel Excel işlendi.\n"
            f"{count} personel güncellendi veya eklendi.\n"
            f"Toplam personel: {personnel_store.count()}\n"
            "Personeller bota DM'den /start yazmalıdır."
        )
    except Exception as e:
        logger.exception("Personel Excel işlenemedi")
        await update.message.reply_text(f"❌ Excel işlenirken hata oluştu: {e}")
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


async def chatid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    allowed = chat.id == config.target_chat_id
    await update.message.reply_text(
        f"Sohbet ID: {chat.id}\n"
        f"Bu ID şu anda TELEGRAM_GROUP_CHAT_ID olarak kullanılıyor.\n"
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
            uncompleted_only=False,
            **_fetch_kwargs(),
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
        uncompleted_only=config.notify_uncompleted_only,
        **_fetch_kwargs(),
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
    failed_dm = 0
    api_started = time.monotonic()

    try:
        calls = await asyncio.to_thread(
            fetch_missed_calls,
            company_code,
            today,
            today,
            uncompleted_only=config.notify_uncompleted_only,
            **_fetch_kwargs(),
        )
        api_ms = int((time.monotonic() - api_started) * 1000)
        _update_bot_data(
            context,
            last_api_duration_ms=api_ms,
            last_poll_error="-",
        )
    except Exception as exc:
        logger.warning("Anlık kaçan çağrı kontrolü başarısız: %s", exc)
        _update_bot_data(context, last_poll_error=str(exc))
        return

    dahili_cache = await asyncio.to_thread(build_phone_dahili_cache, company_code, 15)

    for call in calls:
        notify_ctx = build_missed_call_context(
            call,
            dahili_cache=dahili_cache,
            personnel_store=personnel_store,
            sent_store=sent_store,
        )
        if notify_ctx is None:
            continue

        private_ok, group_ok = await deliver_missed_call_notification(
            notify_ctx,
            bot=context.bot,
            target_chat_id=config.target_chat_id,
        )

        if counts_as_failed_dm(notify_ctx, private_ok):
            failed_dm += 1

        if group_ok and not notify_ctx.retry_private_only:
            sent_store.mark_group_notified(notify_ctx.key, save=False)

        if should_mark_complete(notify_ctx, private_ok=private_ok, group_ok=group_ok):
            sent_store.mark_complete(notify_ctx.key, save=False)
            sent_now += 1

    sent_store.flush()

    _update_bot_data(
        context,
        last_poll_count=sent_now,
        last_poll_time=dtm.now().strftime("%d.%m.%Y %H:%M:%S"),
        failed_dm_count=context.bot_data.get("failed_dm_count", 0) + failed_dm
        if context.bot_data
        else failed_dm,
    )


async def purge_old_sent_calls(context: ContextTypes.DEFAULT_TYPE) -> None:
    removed = sent_store.purge_old()
    if removed:
        logger.info("Periyodik temizlik: %s eski dedup kaydı silindi.", removed)


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
    private_only = filters.ChatType.PRIVATE

    application = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .build()
    )

    application.bot_data.setdefault("failed_dm_count", 0)

    application.add_handler(TypeHandler(Update, log_update), group=-1)
    application.add_handler(CommandHandler("start", private_start_command, filters=private_only))
    application.add_handler(CommandHandler("ping", ping_command, filters=group_only))
    application.add_handler(CommandHandler("chatid", chatid_command, filters=group_only))
    application.add_handler(CommandHandler("start", start_command, filters=allowed))
    application.add_handler(CommandHandler("help", start_command, filters=allowed))
    application.add_handler(CommandHandler("ayar", ayar_command, filters=allowed))
    application.add_handler(CommandHandler("stats", stats_command, filters=allowed))
    application.add_handler(CommandHandler("temizle", temizle_command, filters=allowed))
    application.add_handler(CommandHandler("firmakodu", firmakodu_command, filters=allowed))
    application.add_handler(CommandHandler("kuyruklar", kuyruklar_command, filters=allowed))
    application.add_handler(CommandHandler("kacancagri", kacancagri_command, filters=allowed))
    application.add_handler(CommandHandler("personelekle", personelekle_command, filters=allowed))
    application.add_handler(CommandHandler("personelsil", personelsil_command, filters=allowed))
    application.add_handler(CommandHandler("personeller", personeller_command, filters=allowed))
    application.add_handler(MessageHandler(filters.Document.ALL & allowed, personel_excel_handler))

    application.add_error_handler(error_handler)

    application.job_queue.run_repeating(
        poll_missed_calls,
        interval=config.polling_interval_seconds,
        first=5,
        name="missed-call-poller",
    )
    application.job_queue.run_daily(
        purge_old_sent_calls,
        time=dt_time(hour=3, minute=0),
        name="sent-store-purge",
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
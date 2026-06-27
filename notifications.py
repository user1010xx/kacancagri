from dataclasses import dataclass
from enum import Enum
from typing import Any

from invekto_client import _call_datetime, _normalize_phone, call_key


class NotifyKind(str, Enum):
    NO_DAHILI = "no_dahili"
    NO_PERSONNEL = "no_personnel"
    PERSONNEL = "personnel"


@dataclass
class MissedCallContext:
    key: str
    phone: str
    call_time_str: str
    dahili: str | None
    personnel: dict[str, str] | None
    kind: NotifyKind
    retry_private_only: bool


def build_call_time_str(call: dict[str, Any]) -> str:
    call_date, call_time = _call_datetime(call)
    return f"{call_date} {call_time}".strip() or "Bilinmiyor"


def build_missed_call_context(
    call: dict[str, Any],
    *,
    dahili_cache: dict[str, str],
    personnel_store,
    sent_store,
) -> MissedCallContext | None:
    key = call_key(call)
    if sent_store.is_complete(key):
        return None

    phone = str(call.get("Phone") or "")
    call_time_str = build_call_time_str(call)
    retry_private_only = sent_store.is_group_notified(key)

    dahili = dahili_cache.get(_normalize_phone(phone))
    if not dahili:
        return MissedCallContext(
            key=key,
            phone=phone,
            call_time_str=call_time_str,
            dahili=None,
            personnel=None,
            kind=NotifyKind.NO_DAHILI,
            retry_private_only=False,
        )

    personnel = personnel_store.find_for_extension(dahili)
    if not personnel:
        return MissedCallContext(
            key=key,
            phone=phone,
            call_time_str=call_time_str,
            dahili=dahili,
            personnel=None,
            kind=NotifyKind.NO_PERSONNEL,
            retry_private_only=False,
        )

    return MissedCallContext(
        key=key,
        phone=phone,
        call_time_str=call_time_str,
        dahili=dahili,
        personnel=personnel,
        kind=NotifyKind.PERSONNEL,
        retry_private_only=retry_private_only,
    )


def private_chat_id(personnel: dict[str, str]) -> int | None:
    raw = str(personnel.get("telegram_chat_id", "")).strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _format_personel_name(name: str) -> str:
    text = str(name).strip()
    if not text:
        return "Personel"

    def _cap(part: str) -> str:
        return part[:1].upper() + part[1:].lower() if part else part

    formatted: list[str] = []
    for token in text.split():
        if "-" in token:
            formatted.append("-".join(_cap(p) for p in token.split("-")))
        else:
            formatted.append(_cap(token))
    return " ".join(formatted)


def build_private_text(personel_adi: str, phone: str, call_time_str: str) -> str:
    display_name = _format_personel_name(personel_adi)
    return (
        "🔴 Kaçan Çağrı\n\n"
        f"👤 Personel: {display_name}\n"
        f"📞 Telefon: {phone}\n"
        f"🕐 Arama: {call_time_str}\n\n"
        "Üye adayımızı arar mısınız?"
    )


def build_group_text(ctx: MissedCallContext, *, private_ok: bool) -> str:
    if ctx.kind == NotifyKind.NO_DAHILI:
        return (
            "🔴 Kaçan Çağrı\n\n"
            f"📞 Telefon: {ctx.phone}\n"
            f"🕐 Arama Saati: {ctx.call_time_str}\n"
            "ℹ️ Son 15 günde eşleşen personel bulunamadı."
        )

    if ctx.kind == NotifyKind.NO_PERSONNEL:
        return (
            "🔴 Kaçan Çağrı\n\n"
            f"📞 Telefon: {ctx.phone}\n"
            f"🕐 Arama Saati: {ctx.call_time_str}\n"
            f"⚠️ Dahili {ctx.dahili} için personel kaydı bulunamadı."
        )

    personnel = ctx.personnel or {}
    personel_adi = personnel.get("personel_adi", ctx.dahili or "")
    tg_username = personnel.get("telegram_username", "")
    chat_id = private_chat_id(personnel)

    if not chat_id:
        info = f"@{tg_username} bota /start demedi (DM gönderilemedi)"
    elif private_ok:
        info = f"@{tg_username} e iletildi."
    else:
        info = f"@{tg_username} e iletilemedi!"

    return (
        "Kaçan çağrı\n"
        f"- Personel : {personel_adi}\n"
        f"- Numara : {ctx.phone}\n"
        f"- Arama saati : {ctx.call_time_str}\n"
        f"- İnfo : {info}"
    )


def should_mark_complete(ctx: MissedCallContext, *, private_ok: bool, group_ok: bool) -> bool:
    if not group_ok:
        return False
    if ctx.kind == NotifyKind.PERSONNEL:
        return private_ok
    return True


def counts_as_failed_dm(ctx: MissedCallContext, private_ok: bool) -> bool:
    return ctx.kind == NotifyKind.PERSONNEL and not private_ok


async def deliver_missed_call_notification(
    ctx: MissedCallContext,
    *,
    bot,
    target_chat_id: int,
) -> tuple[bool, bool]:
    """Özel ve grup bildirimini gönderir. (private_ok, group_ok) döner."""
    private_ok = False

    if ctx.kind == NotifyKind.PERSONNEL:
        personnel = ctx.personnel or {}
        personel_adi = personnel.get("personel_adi", ctx.dahili or "")
        private_text = build_private_text(personel_adi, ctx.phone, ctx.call_time_str)
        chat_id = private_chat_id(personnel)
        if chat_id:
            try:
                await bot.send_message(chat_id=chat_id, text=private_text)
                private_ok = True
            except Exception:
                private_ok = False

    group_ok = False
    if ctx.retry_private_only:
        if private_ok:
            group_text = build_group_text(ctx, private_ok=True)
            try:
                await bot.send_message(chat_id=target_chat_id, text=group_text)
                group_ok = True
            except Exception:
                group_ok = False
        else:
            group_ok = True
    else:
        group_text = build_group_text(ctx, private_ok=private_ok)
        try:
            await bot.send_message(chat_id=target_chat_id, text=group_text)
            group_ok = True
        except Exception:
            group_ok = False

    return private_ok, group_ok
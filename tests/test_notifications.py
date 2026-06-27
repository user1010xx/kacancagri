from notifications import (
    NotifyKind,
    build_group_text,
    build_missed_call_context,
    private_chat_id,
    should_mark_complete,
)
from personnel_store import PersonnelStore


class _FakeSentStore:
    def __init__(self):
        self.completed = set()
        self.group_notified = set()

    def is_complete(self, key: str) -> bool:
        return key in self.completed

    def is_group_notified(self, key: str) -> bool:
        return key in self.group_notified


def test_build_context_no_dahili(tmp_path):
    sent = _FakeSentStore()
    personnel = PersonnelStore(tmp_path / "p.json")
    call = {
        "ID": "1",
        "Phone": "905551112233",
        "ChekInDate": "2026-06-26",
        "ChekInTime": "18:35:00",
        "Queue": "Gelen Arama",
        "Status": "2",
    }
    ctx = build_missed_call_context(
        call,
        dahili_cache={},
        personnel_store=personnel,
        sent_store=sent,
    )
    assert ctx is not None
    assert ctx.kind == NotifyKind.NO_DAHILI


def test_should_mark_complete_rules():
    ctx_personnel = type("C", (), {"kind": NotifyKind.PERSONNEL})()
    ctx_other = type("C", (), {"kind": NotifyKind.NO_DAHILI})()

    assert should_mark_complete(ctx_personnel, private_ok=True, group_ok=True)
    assert not should_mark_complete(ctx_personnel, private_ok=False, group_ok=True)
    assert should_mark_complete(ctx_other, private_ok=False, group_ok=True)


def test_private_chat_id():
    assert private_chat_id({"telegram_chat_id": "123"}) == 123
    assert private_chat_id({"telegram_chat_id": ""}) is None


def test_group_text_dm_not_ready():
    ctx = type(
        "C",
        (),
        {
            "kind": NotifyKind.PERSONNEL,
            "phone": "905551112233",
            "call_time_str": "26.06.2026 18:35",
            "dahili": "105",
            "personnel": {
                "personel_adi": "Ali",
                "telegram_username": "ali",
                "telegram_chat_id": "",
            },
        },
    )()
    text = build_group_text(ctx, private_ok=False)
    assert "/start" in text
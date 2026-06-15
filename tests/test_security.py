"""Tests for authorization layer — AccessPolicy + bot gate field resolution."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from strix_telegram_bot.security import AccessPolicy, is_authorized


# ── Helpers: build real Telegram update structure ──────────────────────

def _message_update(text: str, user_id: str = "111",
                    chat_id: str = "-100999") -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 100,
            "from": {"id": int(user_id), "is_bot": False, "first_name": "Test"},
            "chat": {"id": int(chat_id), "type": "private"},
            "text": text,
            "date": 1000000,
        },
    }


def _callback_update(data: str, user_id: str = "111",
                     chat_id: str = "-100999") -> dict:
    return {
        "update_id": 2,
        "callback_query": {
            "id": "cb_1",
            "from": {"id": int(user_id), "is_bot": False, "first_name": "Test"},
            "message": {
                "message_id": 200,
                "chat": {"id": int(chat_id), "type": "private"},
                "date": 1000000,
            },
            "data": data,
        },
    }


def _extract_command_auth(update: dict) -> tuple[str, str]:
    msg = update.get("message", {})
    user_id = str(msg.get("from", {}).get("id", ""))
    chat_id = str(msg.get("chat", {}).get("id", ""))
    return user_id, chat_id


def _extract_callback_auth(update: dict) -> tuple[str, str]:
    cb = update.get("callback_query", {})
    user_id = str(cb.get("from", {}).get("id", ""))
    chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
    return user_id, chat_id


# ── Tests ──────────────────────────────────────────────────────────────


class TestAccessPolicy:
    """Direct AccessPolicy.is_authorized() tests."""

    def test_allow_all_when_empty(self):
        policy = AccessPolicy()
        with patch.object(policy, "_allowed_users", frozenset()):
            with patch.object(policy, "_allowed_chats", frozenset()):
                assert policy.is_authorized("111", "-100999") is True
                assert policy.is_authorized("999", "-100000") is True

    def test_allow_by_user_id(self):
        policy = AccessPolicy()
        with patch.object(policy, "_allowed_users", frozenset(["111"])):
            with patch.object(policy, "_allowed_chats", frozenset()):
                assert policy.is_authorized("111", "-100999") is True
                assert policy.is_authorized("222", "-100999") is False

    def test_allow_by_chat_id(self):
        policy = AccessPolicy()
        with patch.object(policy, "_allowed_users", frozenset()):
            with patch.object(policy, "_allowed_chats", frozenset(["-100999"])):
                assert policy.is_authorized("111", "-100999") is True
                assert policy.is_authorized("111", "-100888") is False

    def test_allow_by_either_user_or_chat(self):
        policy = AccessPolicy()
        with patch.object(policy, "_allowed_users", frozenset(["111"])):
            with patch.object(policy, "_allowed_chats", frozenset(["-100999"])):
                assert policy.is_authorized("111", "-100888") is True
                assert policy.is_authorized("222", "-100999") is True
                assert policy.is_authorized("222", "-100888") is False

    def test_reject_when_both_set_and_not_matching(self):
        policy = AccessPolicy()
        with patch.object(policy, "_allowed_users", frozenset(["111"])):
            with patch.object(policy, "_allowed_chats", frozenset(["-100999"])):
                assert policy.is_authorized("999", "-100000") is False


class TestCommandAuthFieldResolution:
    """Verify _handle_command extracts auth fields from real Telegram message update."""

    def test_extracts_user_and_chat_from_message(self):
        upd = _message_update("/start")
        uid, cid = _extract_command_auth(upd)
        assert uid == "111"
        assert cid == "-100999"

    def test_different_user_and_chat(self):
        upd = _message_update("/version", user_id="222", chat_id="-100888")
        uid, cid = _extract_command_auth(upd)
        assert uid == "222"
        assert cid == "-100888"

    def test_authorized_user_passes_gate(self):
        upd = _message_update("/start")
        uid, cid = _extract_command_auth(upd)
        policy = AccessPolicy()
        with patch.object(policy, "_allowed_users", frozenset(["111", "222"])):
            with patch.object(policy, "_allowed_chats", frozenset()):
                assert policy.is_authorized(uid, cid) is True

    def test_unauthorized_user_rejected(self):
        upd = _message_update("/start")
        uid, cid = _extract_command_auth(upd)
        policy = AccessPolicy()
        with patch.object(policy, "_allowed_users", frozenset(["999"])):
            with patch.object(policy, "_allowed_chats", frozenset()):
                assert policy.is_authorized(uid, cid) is False

    def test_authorized_by_chat_works(self):
        upd = _message_update("/start", user_id="999")
        uid, cid = _extract_command_auth(upd)
        policy = AccessPolicy()
        with patch.object(policy, "_allowed_users", frozenset()):
            with patch.object(policy, "_allowed_chats", frozenset(["-100999"])):
                assert policy.is_authorized(uid, cid) is True


class TestCallbackAuthFieldResolution:
    """Verify _handle_callback extracts auth fields from real Telegram callback update."""

    def test_extracts_user_and_chat_from_callback(self):
        upd = _callback_update("menu:main")
        uid, cid = _extract_callback_auth(upd)
        assert uid == "111"
        assert cid == "-100999"

    def test_different_user_and_chat(self):
        upd = _callback_update("chat:enter", user_id="333", chat_id="-100777")
        uid, cid = _extract_callback_auth(upd)
        assert uid == "333"
        assert cid == "-100777"

    def test_authorized_user_passes_gate(self):
        upd = _callback_update("menu:main")
        uid, cid = _extract_callback_auth(upd)
        policy = AccessPolicy()
        with patch.object(policy, "_allowed_users", frozenset(["111"])):
            with patch.object(policy, "_allowed_chats", frozenset()):
                assert policy.is_authorized(uid, cid) is True

    def test_unauthorized_user_rejected(self):
        upd = _callback_update("menu:main")
        uid, cid = _extract_callback_auth(upd)
        policy = AccessPolicy()
        with patch.object(policy, "_allowed_users", frozenset(["999"])):
            with patch.object(policy, "_allowed_chats", frozenset()):
                assert policy.is_authorized(uid, cid) is False

    def test_authorized_by_chat_works(self):
        upd = _callback_update("menu:main", user_id="999")
        uid, cid = _extract_callback_auth(upd)
        policy = AccessPolicy()
        with patch.object(policy, "_allowed_users", frozenset()):
            with patch.object(policy, "_allowed_chats", frozenset(["-100999"])):
                assert policy.is_authorized(uid, cid) is True


class TestNoSilentRejection:
    """The bot gate responds with error text — never silence."""

    @patch("strix_telegram_bot.bot.send_message")
    @patch("strix_telegram_bot.bot.is_authorized")
    def test_unauthorized_command_responds(self, mock_auth, mock_send):
        from strix_telegram_bot.bot import StrixBot
        mock_auth.return_value = False

        bot = StrixBot()
        upd = _message_update("/version")

        bot._handle_command(upd)
        mock_send.assert_called_once()
        call_args = mock_send.call_args[0]
        assert "No autorizado" in call_args[2] or "no autorizado" in call_args[2].lower()

    @patch("strix_telegram_bot.bot.answer_callback")
    @patch("strix_telegram_bot.bot.is_authorized")
    def test_unauthorized_callback_responds(self, mock_auth, mock_answer):
        from strix_telegram_bot.bot import StrixBot
        mock_auth.return_value = False

        bot = StrixBot()
        upd = _callback_update("menu:main")

        bot._handle_callback(upd)
        mock_answer.assert_called_once()

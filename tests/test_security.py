"""Tests for authorization layer — AccessPolicy + bot gate field resolution."""

from __future__ import annotations

from unittest.mock import patch

from strix_telegram_bot.security import AccessPolicy

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
    """Direct AccessPolicy.is_authorized() tests (fail-closed + group AND)."""

    def test_deny_all_when_empty(self):
        """Fail-closed: an empty user allowlist denies everyone."""
        policy = AccessPolicy()
        with patch.object(policy, "_allowed_users", frozenset()):
            with patch.object(policy, "_allowed_chats", frozenset()):
                assert policy.is_authorized("111", "-100999") is False
                assert policy.is_authorized("999", "-100000") is False

    def test_validate_flags_empty_user_allowlist(self):
        policy = AccessPolicy()
        with patch.object(policy, "_allowed_users", frozenset()):
            assert policy.validate() != []
        with patch.object(policy, "_allowed_users", frozenset(["111"])):
            assert policy.validate() == []

    def test_allow_by_user_id_private(self):
        policy = AccessPolicy()
        with patch.object(policy, "_allowed_users", frozenset(["111"])):
            with patch.object(policy, "_allowed_chats", frozenset()):
                assert policy.is_authorized("111", "-100999", "private") is True
                assert policy.is_authorized("222", "-100999", "private") is False

    def test_chat_alone_does_not_authorize(self):
        """A chat in the allowlist does NOT authorize a user that is not."""
        policy = AccessPolicy()
        with patch.object(policy, "_allowed_users", frozenset()):
            with patch.object(policy, "_allowed_chats", frozenset(["-100999"])):
                assert policy.is_authorized("111", "-100999", "private") is False

    def test_group_requires_user_and_chat(self):
        policy = AccessPolicy()
        with patch.object(policy, "_allowed_users", frozenset(["111"])):
            with patch.object(policy, "_allowed_chats", frozenset(["-100999"])):
                # allowed user + allowed group → True
                assert policy.is_authorized("111", "-100999", "group") is True
                # allowed user + NOT allowed group → False
                assert policy.is_authorized("111", "-100888", "group") is False
                # NOT allowed user + allowed group → False
                assert policy.is_authorized("222", "-100999", "group") is False
                # NOT allowed user + NOT allowed group → False
                assert policy.is_authorized("222", "-100888", "group") is False

    def test_supergroup_requires_user_and_chat(self):
        policy = AccessPolicy()
        with patch.object(policy, "_allowed_users", frozenset(["111"])):
            with patch.object(policy, "_allowed_chats", frozenset(["-100999"])):
                assert policy.is_authorized("111", "-100999", "supergroup") is True
                assert policy.is_authorized("111", "-100888", "supergroup") is False

    def test_private_ignores_chat_allowlist(self):
        """In a private chat the chat allowlist is irrelevant; only the user matters."""
        policy = AccessPolicy()
        with patch.object(policy, "_allowed_users", frozenset(["111"])):
            with patch.object(policy, "_allowed_chats", frozenset(["-100999"])):
                # allowed user in a chat that is NOT allowlisted → still True (private)
                assert policy.is_authorized("111", "-100888", "private") is True

    def test_reject_when_user_not_matching(self):
        policy = AccessPolicy()
        with patch.object(policy, "_allowed_users", frozenset(["111"])):
            with patch.object(policy, "_allowed_chats", frozenset(["-100999"])):
                assert policy.is_authorized("999", "-100000", "private") is False


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

    def test_chat_alone_does_not_authorize(self):
        """New fail-closed: a chat in the allowlist does not authorize a user that is not."""
        upd = _message_update("/start", user_id="999")
        uid, cid = _extract_command_auth(upd)
        policy = AccessPolicy()
        with patch.object(policy, "_allowed_users", frozenset()):
            with patch.object(policy, "_allowed_chats", frozenset(["-100999"])):
                assert policy.is_authorized(uid, cid) is False


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

    def test_chat_alone_does_not_authorize(self):
        """New fail-closed: a chat in the allowlist does not authorize a user that is not."""
        upd = _callback_update("menu:main", user_id="999")
        uid, cid = _extract_callback_auth(upd)
        policy = AccessPolicy()
        with patch.object(policy, "_allowed_users", frozenset()):
            with patch.object(policy, "_allowed_chats", frozenset(["-100999"])):
                assert policy.is_authorized(uid, cid) is False


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

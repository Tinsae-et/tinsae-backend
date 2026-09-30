"""Shop <-> Telegram chat association.

The state changes live in LinkServiceTests; the PTB wiring is LinkHandlerTests'
job, in the same split the rest of `bot` uses.
"""

from datetime import timedelta
from unittest import mock

from asgiref.sync import async_to_sync
from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from bot.management.commands.run_bot import link, on_callback, on_text, start, unlink
from bot.models import ShopLinkCode
from bot.services import (
    AFFIRMATIVE,
    NEGATIVE,
    clear_blocked_for_chat,
    issue_link_code,
    link_shop_to_chat,
    looks_like_link_code,
    normalise_link_code,
    resolve_link_code,
    unlink_chat,
)
from shops.models import Shop

User = get_user_model()

CHAT_ID = "987654321"
OTHER_CHAT_ID = "111000111"


@override_settings(TELEGRAM_BOT_TOKEN="test-token")
class LinkCodeTestCase(TestCase):
    """A shop with no chat yet, and one live code for it."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="merchant",
            email="merchant@example.com",
            password="password123",
        )
        self.shop = Shop.objects.create(name="Bole Electronics", owner=self.user)
        self.code = issue_link_code(self.shop, created_by=self.user)


class LinkServiceTests(LinkCodeTestCase):
    def test_a_valid_code_binds_the_chat_to_the_shop(self):
        outcome, shop = link_shop_to_chat(self.code.code, CHAT_ID)

        self.assertEqual(outcome, "linked")
        self.assertEqual(shop.pk, self.shop.pk)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.telegram_chat_id, CHAT_ID)

    def test_the_code_is_spent_and_records_who_spent_it(self):
        link_shop_to_chat(self.code.code, CHAT_ID)

        self.code.refresh_from_db()
        self.assertIsNotNone(self.code.used_at)
        self.assertEqual(self.code.used_by_chat_id, CHAT_ID)
        self.assertFalse(self.code.is_live)

    def test_an_unknown_code_changes_nothing(self):
        for attempt in ("TIN-ZZZZZZZ", "not-a-code", ""):
            outcome, shop = link_shop_to_chat(attempt, CHAT_ID)
            self.assertEqual(outcome, "unknown")
            self.assertIsNone(shop)

        self.shop.refresh_from_db()
        self.assertEqual(self.shop.telegram_chat_id, "")

    def test_a_spent_code_cannot_be_replayed_from_another_chat(self):
        """The leaked-code case: possession after the fact buys nothing."""
        link_shop_to_chat(self.code.code, CHAT_ID)

        outcome, shop = link_shop_to_chat(self.code.code, OTHER_CHAT_ID)

        self.assertEqual(outcome, "used")
        self.assertIsNone(shop)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.telegram_chat_id, CHAT_ID)

    def test_resending_the_same_code_from_the_same_chat_is_friendly(self):
        """A double tap on send is not an error and must not read as one."""
        link_shop_to_chat(self.code.code, CHAT_ID)

        outcome, shop = link_shop_to_chat(self.code.code, CHAT_ID)

        self.assertEqual(outcome, "already_linked")
        self.assertEqual(shop.pk, self.shop.pk)

    def test_an_expired_code_changes_nothing(self):
        ShopLinkCode.objects.filter(pk=self.code.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )

        outcome, shop = link_shop_to_chat(self.code.code, CHAT_ID)

        self.assertEqual(outcome, "expired")
        self.assertIsNone(shop)
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.telegram_chat_id, "")

    def test_a_new_code_moves_the_shop_to_a_new_chat(self):
        link_shop_to_chat(self.code.code, CHAT_ID)
        replacement = issue_link_code(self.shop)

        outcome, _ = link_shop_to_chat(replacement.code, OTHER_CHAT_ID)

        self.assertEqual(outcome, "relinked")
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.telegram_chat_id, OTHER_CHAT_ID)

    def test_one_chat_can_link_two_shops(self):
        """`shops_for_chat` returns a list on purpose; linking must not undo that."""
        second = Shop.objects.create(name="Piassa Electronics", owner=self.user)

        link_shop_to_chat(self.code.code, CHAT_ID)
        link_shop_to_chat(issue_link_code(second).code, CHAT_ID)

        self.assertEqual(
            list(
                Shop.objects.filter(telegram_chat_id=CHAT_ID)
                .order_by("name")
                .values_list("name", flat=True)
            ),
            ["Bole Electronics", "Piassa Electronics"],
        )

    def test_issuing_a_code_retires_the_previous_one(self):
        """One live code per shop, or the older one fails in a baffling way."""
        replacement = issue_link_code(self.shop)

        self.assertEqual(link_shop_to_chat(self.code.code, CHAT_ID)[0], "expired")
        self.assertEqual(link_shop_to_chat(replacement.code, CHAT_ID)[0], "linked")

    def test_an_empty_chat_cannot_link(self):
        self.assertEqual(link_shop_to_chat(self.code.code, "")[0], "unknown")
        self.assertEqual(link_shop_to_chat(self.code.code, None)[0], "unknown")

        self.shop.refresh_from_db()
        self.assertEqual(self.shop.telegram_chat_id, "")

    def test_unlink_chat_clears_telegram_chat_id_and_blocked_at(self):
        self.shop.telegram_chat_id = CHAT_ID
        self.shop.blocked_at = timezone.now()
        self.shop.save(update_fields=["telegram_chat_id", "blocked_at"])

        second = Shop.objects.create(
            name="Piassa Electronics",
            owner=self.user,
            telegram_chat_id=CHAT_ID,
            blocked_at=timezone.now(),
        )

        unlinked = unlink_chat(CHAT_ID)
        self.assertEqual(len(unlinked), 2)

        self.shop.refresh_from_db()
        self.assertEqual(self.shop.telegram_chat_id, "")
        self.assertIsNone(self.shop.blocked_at)

        second.refresh_from_db()
        self.assertEqual(second.telegram_chat_id, "")
        self.assertIsNone(second.blocked_at)


class LinkCodeShapeTests(LinkCodeTestCase):
    def test_codes_avoid_glyphs_that_are_ambiguous_when_read_aloud(self):
        body = self.code.code.removeprefix("TIN-")

        for banned in "01OIL":
            self.assertNotIn(banned, body)

    def test_the_code_is_accepted_lowercase_spaced_and_unprefixed(self):
        body = self.code.code.removeprefix("TIN-")

        for variant in (self.code.code.lower(), f"  {self.code.code} ", body):
            self.assertEqual(normalise_link_code(variant), self.code.code)

    def test_a_code_can_never_be_read_as_a_yes_or_no_answer(self):
        """The routing guarantee, held against the actual vocabularies."""
        for word in AFFIRMATIVE | NEGATIVE:
            self.assertFalse(looks_like_link_code(word))

    def test_bare_text_must_carry_the_prefix_to_route_as_a_code(self):
        self.assertTrue(looks_like_link_code(self.code.code))
        self.assertFalse(looks_like_link_code(self.code.code.removeprefix("TIN-")))
        self.assertFalse(looks_like_link_code("still available"))

    def test_a_code_is_found_inside_a_sentence(self):
        """Merchants forward the code with words around it."""
        message = f"hello here is my code {self.code.code} thanks"

        self.assertTrue(looks_like_link_code(message))
        self.assertEqual(resolve_link_code(message), self.code.code)

    def test_an_over_long_code_is_not_truncated_into_a_valid_one(self):
        """A typo must fail, not bind the shop to whatever the first 7 spell."""
        self.assertEqual(resolve_link_code(f"{self.code.code}X"), "")

    def test_a_malformed_code_still_routes_to_the_link_path(self):
        """Right shape, wrong alphabet: it was meant as a code, so say so."""
        self.assertTrue(looks_like_link_code("TIN-BADCODE"))
        self.assertEqual(resolve_link_code("TIN-BADCODE"), "")

    def test_clear_blocked_for_chat_clears_blocked_at_for_all_shops_on_chat(self):
        second = Shop.objects.create(
            name="Piassa Electronics",
            owner=self.user,
            telegram_chat_id=CHAT_ID,
            blocked_at=timezone.now(),
        )
        self.shop.telegram_chat_id = CHAT_ID
        self.shop.blocked_at = timezone.now()
        self.shop.save(update_fields=["telegram_chat_id", "blocked_at"])

        count = clear_blocked_for_chat(CHAT_ID)
        self.assertEqual(count, 2)

        self.shop.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNone(self.shop.blocked_at)
        self.assertIsNone(second.blocked_at)

    def test_clear_blocked_for_chat_noop_when_not_blocked(self):
        self.shop.telegram_chat_id = CHAT_ID
        self.shop.blocked_at = None
        self.shop.save(update_fields=["telegram_chat_id", "blocked_at"])

        self.assertEqual(clear_blocked_for_chat(CHAT_ID), 0)
        self.assertEqual(clear_blocked_for_chat(OTHER_CHAT_ID), 0)
        self.assertEqual(clear_blocked_for_chat(""), 0)
        self.assertEqual(clear_blocked_for_chat(None), 0)


class LinkHandlerTests(LinkCodeTestCase):
    """PTB wiring — the state change itself is LinkServiceTests' job."""

    def _message_update(self, text, chat_id=CHAT_ID):
        update = mock.Mock()
        update.message = mock.AsyncMock()
        update.message.text = text
        update.message.reply_to_message = None
        update.effective_chat.id = int(chat_id)
        return update, update.message

    def test_the_command_links_and_names_the_shop(self):
        update, message = self._message_update(f"/link {self.code.code}")

        async_to_sync(link)(update, mock.Mock(args=[self.code.code]))

        self.shop.refresh_from_db()
        self.assertEqual(self.shop.telegram_chat_id, CHAT_ID)
        self.assertIn("Bole Electronics", message.reply_text.call_args.args[0])

    def test_a_bare_pasted_code_links_too(self):
        update, message = self._message_update(self.code.code)

        async_to_sync(on_text)(update, None)

        self.shop.refresh_from_db()
        self.assertEqual(self.shop.telegram_chat_id, CHAT_ID)
        self.assertIn("Bole Electronics", message.reply_text.call_args.args[0])

    def test_a_yes_still_reaches_the_confirmation_path(self):
        """The link route must not swallow ordinary answers."""
        update, message = self._message_update("yes")

        async_to_sync(on_text)(update, None)

        self.shop.refresh_from_db()
        self.assertEqual(self.shop.telegram_chat_id, "")
        self.assertIn("/myproducts", message.reply_text.call_args.args[0])

    def test_the_command_with_no_code_explains_the_format(self):
        update, message = self._message_update("/link")

        async_to_sync(link)(update, mock.Mock(args=[]))

        self.assertIn("/link TIN-", message.reply_text.call_args.args[0])

    def test_an_unknown_code_gets_a_way_forward(self):
        update, message = self._message_update("TIN-ZZZZZZZ")

        async_to_sync(on_text)(update, None)

        self.assertIn("don't recognise", message.reply_text.call_args.args[0])

    def test_a_code_sent_with_words_around_it_still_links(self):
        update, message = self._message_update(f"hi, my code is {self.code.code}")

        async_to_sync(on_text)(update, None)

        self.shop.refresh_from_db()
        self.assertEqual(self.shop.telegram_chat_id, CHAT_ID)
        self.assertIn("Bole Electronics", message.reply_text.call_args.args[0])

    def test_a_malformed_code_is_never_handed_to_the_yes_no_parser(self):
        """Someone typing a code should hear about the code, not about stock."""
        update, message = self._message_update("TIN-BADCODE")

        async_to_sync(on_text)(update, None)

        reply = message.reply_text.call_args.args[0]
        self.assertIn("don't recognise", reply)
        self.assertNotIn("still available", reply)

    def test_the_command_tolerates_words_around_the_code(self):
        args = ["my", "code", "is", self.code.code]
        update, message = self._message_update(f"/link {' '.join(args)}")

        async_to_sync(link)(update, mock.Mock(args=args))

        self.shop.refresh_from_db()
        self.assertEqual(self.shop.telegram_chat_id, CHAT_ID)
        self.assertIn("Bole Electronics", message.reply_text.call_args.args[0])

    def test_start_with_code_links(self):
        update, message = self._message_update(f"/start {self.code.code}")

        async_to_sync(start)(update, mock.Mock(args=[self.code.code]))

        self.shop.refresh_from_db()
        self.assertEqual(self.shop.telegram_chat_id, CHAT_ID)
        self.assertIn("Bole Electronics", message.reply_text.call_args.args[0])

    def test_plain_start_shows_welcome(self):
        update, message = self._message_update("/start")

        async_to_sync(start)(update, mock.Mock(args=[]))

        self.shop.refresh_from_db()
        self.assertEqual(self.shop.telegram_chat_id, "")
        reply = (
            message.reply_text.call_args.kwargs.get("text")
            or message.reply_text.call_args.args[0]
        )
        self.assertIn("Welcome to Tinsae", reply)

    def test_unlink_not_linked(self):
        update, message = self._message_update("/unlink")

        async_to_sync(unlink)(update, mock.Mock(args=[]))

        reply = (
            message.reply_text.call_args.kwargs.get("text")
            or message.reply_text.call_args.args[0]
        )
        self.assertIn("not linked", reply)

    def test_unlink_prompts_confirmation(self):
        self.shop.telegram_chat_id = CHAT_ID
        self.shop.save(update_fields=["telegram_chat_id"])

        update, message = self._message_update("/unlink")

        async_to_sync(unlink)(update, mock.Mock(args=[]))

        self.assertIn("unlink this chat", message.reply_text.call_args.kwargs["text"])
        markup = message.reply_text.call_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "unlink:yes")
        self.assertEqual(markup.inline_keyboard[0][1].callback_data, "unlink:cancel")

    def test_unlink_confirm_button_clears_binding(self):
        self.shop.telegram_chat_id = CHAT_ID
        self.shop.save(update_fields=["telegram_chat_id"])

        update = mock.Mock()
        update.effective_chat.id = int(CHAT_ID)
        query = mock.AsyncMock()
        query.data = "unlink:yes"
        query.message = mock.AsyncMock()
        query.message.chat.id = int(CHAT_ID)
        update.callback_query = query

        async_to_sync(on_callback)(update, mock.Mock())

        self.shop.refresh_from_db()
        self.assertEqual(self.shop.telegram_chat_id, "")
        query.answer.assert_awaited()
        self.assertIn("unlinked", query.edit_message_text.call_args.args[0])

    def test_unlink_cancel_does_nothing(self):
        self.shop.telegram_chat_id = CHAT_ID
        self.shop.save(update_fields=["telegram_chat_id"])

        update = mock.Mock()
        update.effective_chat.id = int(CHAT_ID)
        query = mock.AsyncMock()
        query.data = "unlink:cancel"
        query.message = mock.AsyncMock()
        query.message.chat.id = int(CHAT_ID)
        update.callback_query = query

        async_to_sync(on_callback)(update, mock.Mock())

        self.shop.refresh_from_db()
        self.assertEqual(self.shop.telegram_chat_id, CHAT_ID)
        query.answer.assert_awaited()
        self.assertIn("cancelled", query.edit_message_text.call_args.args[0])


class LinkAdminTests(LinkCodeTestCase):
    def test_the_shop_admin_shows_whether_a_shop_is_linked(self):
        shop_admin = site._registry[Shop]

        self.assertIn("telegram_linked", shop_admin.list_display)
        self.assertFalse(shop_admin.telegram_linked(self.shop))

        link_shop_to_chat(self.code.code, CHAT_ID)
        self.shop.refresh_from_db()

        self.assertTrue(shop_admin.telegram_linked(self.shop))

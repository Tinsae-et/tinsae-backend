"""Shop <-> Telegram chat association.

The state changes live in LinkServiceTests; the PTB wiring is LinkHandlerTests'
job, in the same split the rest of `bot` uses.
"""

import time
from datetime import timedelta
from unittest import mock

from asgiref.sync import async_to_sync
from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.core.cache import cache
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
from bot.throttle import (
    clear_link_failures,
    record_link_failure,
)
from shops.models import Shop

User = get_user_model()

CHAT_ID = "987654321"
OTHER_CHAT_ID = "111000111"


@override_settings(TELEGRAM_BOT_TOKEN="test-token")
class LinkCodeTestCase(TestCase):
    """A shop with no chat yet, and one live code for it."""

    def setUp(self):
        cache.clear()
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


class LinkThrottleTests(LinkCodeTestCase):
    """P1 & P2: Link-code failed-attempt throttling and shared cache integration."""

    def test_five_failed_codes_throttle_sixth_attempt(self):
        """5 failed codes from a chat -> the 6th attempt (even a valid code) is
        'throttled' and nothing is linked."""
        for i in range(5):
            outcome, shop = link_shop_to_chat(f"TIN-BAD{i}AA", CHAT_ID)
            self.assertEqual(outcome, "unknown")
            self.assertIsNone(shop)

        # 6th attempt with the valid code
        outcome, shop = link_shop_to_chat(self.code.code, CHAT_ID)
        self.assertEqual(outcome, "throttled")
        self.assertIsNone(shop)

        # Invariants: nothing linked, code not spent
        self.shop.refresh_from_db()
        self.assertEqual(self.shop.telegram_chat_id, "")
        self.code.refresh_from_db()
        self.assertIsNone(self.code.used_at)
        self.assertTrue(self.code.is_live)

    def test_successful_link_clears_the_counter(self):
        """A successful link clears the failed attempt counter."""
        for i in range(4):
            outcome, _ = link_shop_to_chat(f"TIN-BAD{i}AA", CHAT_ID)
            self.assertEqual(outcome, "unknown")

        self.assertEqual(cache.get(f"linkfail:{CHAT_ID}"), 4)

        # Successful link
        outcome, shop = link_shop_to_chat(self.code.code, CHAT_ID)
        self.assertEqual(outcome, "linked")
        self.assertEqual(shop.pk, self.shop.pk)

        # Counter is cleared in cache
        self.assertIsNone(cache.get(f"linkfail:{CHAT_ID}"))

    def test_already_linked_and_relinked_clear_throttle_counter(self):
        """Both already_linked and relinked outcomes clear the throttle counter."""
        # 1. already_linked clears counter
        link_shop_to_chat(self.code.code, CHAT_ID)
        cache.set(f"linkfail:{CHAT_ID}", 2)
        outcome, _ = link_shop_to_chat(self.code.code, CHAT_ID)
        self.assertEqual(outcome, "already_linked")
        self.assertIsNone(cache.get(f"linkfail:{CHAT_ID}"))

        # 2. relinked clears counter
        second_code = issue_link_code(self.shop)
        cache.set(f"linkfail:{OTHER_CHAT_ID}", 3)
        outcome, _ = link_shop_to_chat(second_code.code, OTHER_CHAT_ID)
        self.assertEqual(outcome, "relinked")
        self.assertIsNone(cache.get(f"linkfail:{OTHER_CHAT_ID}"))

    def test_expired_and_used_codes_increment_failure_counter(self):
        """Expired and used code attempts count as failures toward throttle."""
        # Expired code counts as failure
        ShopLinkCode.objects.filter(pk=self.code.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        outcome, _ = link_shop_to_chat(self.code.code, CHAT_ID)
        self.assertEqual(outcome, "expired")
        self.assertEqual(cache.get(f"linkfail:{CHAT_ID}"), 1)

        # Used code replayed by another chat counts as failure
        new_code = issue_link_code(self.shop)
        link_shop_to_chat(new_code.code, OTHER_CHAT_ID)
        outcome, _ = link_shop_to_chat(new_code.code, CHAT_ID)
        self.assertEqual(outcome, "used")
        self.assertEqual(cache.get(f"linkfail:{CHAT_ID}"), 2)

    def test_different_chat_is_unaffected_by_throttle(self):
        """Throttling is chat-scoped: one chat's failures do not affect another."""
        for i in range(5):
            link_shop_to_chat(f"TIN-BAD{i}AA", CHAT_ID)

        # CHAT_ID is throttled
        self.assertEqual(link_shop_to_chat(self.code.code, CHAT_ID)[0], "throttled")

        # OTHER_CHAT_ID can still link successfully
        outcome, shop = link_shop_to_chat(self.code.code, OTHER_CHAT_ID)
        self.assertEqual(outcome, "linked")
        self.assertEqual(shop.pk, self.shop.pk)

    @override_settings(
        TELEGRAM_LINK_FAILURE_LIMIT=2,
        TELEGRAM_LINK_FAILURE_WINDOW_SECONDS=1,
    )
    def test_window_resets_counter(self):
        """After window expires, throttle resets and new attempts are accepted."""
        for i in range(2):
            link_shop_to_chat(f"TIN-BAD{i}AA", CHAT_ID)

        # Throttled on 3rd attempt
        self.assertEqual(link_shop_to_chat(self.code.code, CHAT_ID)[0], "throttled")

        # Sleep past the 1s window
        time.sleep(1.1)

        # Now attempt goes through and succeeds
        outcome, shop = link_shop_to_chat(self.code.code, CHAT_ID)
        self.assertEqual(outcome, "linked")
        self.assertEqual(shop.pk, self.shop.pk)

    def test_throttle_works_through_cache_api(self):
        """P2: Verify throttling interacts through Django's cache API."""
        with mock.patch("bot.throttle.cache") as mock_cache:
            mock_cache.get.return_value = 5

            outcome, shop = link_shop_to_chat(self.code.code, CHAT_ID)
            self.assertEqual(outcome, "throttled")
            self.assertIsNone(shop)
            mock_cache.get.assert_called_with(f"linkfail:{CHAT_ID}", 0)

    def test_record_failure_sets_and_increments_cache(self):
        """P2: Verify record_link_failure uses cache.add and cache.incr."""
        with mock.patch("bot.throttle.cache") as mock_cache:
            # When key does not exist yet (add returns True)
            mock_cache.add.return_value = True
            record_link_failure(CHAT_ID)
            mock_cache.add.assert_called_once_with(
                f"linkfail:{CHAT_ID}", 1, timeout=3600
            )
            mock_cache.incr.assert_not_called()

        with mock.patch("bot.throttle.cache") as mock_cache:
            # When key already exists (add returns False)
            mock_cache.add.return_value = False
            record_link_failure(CHAT_ID)
            mock_cache.incr.assert_called_once_with(f"linkfail:{CHAT_ID}")

        with mock.patch("bot.throttle.cache") as mock_cache:
            clear_link_failures(CHAT_ID)
            mock_cache.delete.assert_called_once_with(f"linkfail:{CHAT_ID}")

    def test_redis_cache_backend_support(self):
        """P2: Verify that RedisCache backend works correctly when configured."""
        import redis
        from django.core.cache.backends.redis import RedisCache

        try:
            r_cache = RedisCache("redis://redis:6379/1", {})
            key = f"linkfail:test_{CHAT_ID}"
            r_cache.delete(key)
            self.assertTrue(r_cache.add(key, 1, timeout=60))
            self.assertEqual(r_cache.get(key), 1)
            self.assertEqual(r_cache.incr(key), 2)
            self.assertEqual(r_cache.get(key), 2)
            r_cache.delete(key)
            self.assertIsNone(r_cache.get(key))
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError):
            pass


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

    def test_throttled_link_command_replies_with_throttled_message(self):
        for i in range(5):
            link_shop_to_chat(f"TIN-BAD{i}AA", CHAT_ID)

        update, message = self._message_update(f"/link {self.code.code}")
        async_to_sync(link)(update, mock.Mock(args=[self.code.code]))

        self.shop.refresh_from_db()
        self.assertEqual(self.shop.telegram_chat_id, "")
        message.reply_text.assert_called_once_with(
            "Too many attempts. Please wait a while and try again."
        )

    def test_throttled_start_with_code_replies_with_throttled_message(self):
        for i in range(5):
            link_shop_to_chat(f"TIN-BAD{i}AA", CHAT_ID)

        update, message = self._message_update(f"/start {self.code.code}")
        async_to_sync(start)(update, mock.Mock(args=[self.code.code]))

        self.shop.refresh_from_db()
        self.assertEqual(self.shop.telegram_chat_id, "")
        message.reply_text.assert_called_once_with(
            "Too many attempts. Please wait a while and try again."
        )

    def test_throttled_bare_code_replies_with_throttled_message(self):
        for i in range(5):
            link_shop_to_chat(f"TIN-BAD{i}AA", CHAT_ID)

        update, message = self._message_update(self.code.code)
        async_to_sync(on_text)(update, None)

        self.shop.refresh_from_db()
        self.assertEqual(self.shop.telegram_chat_id, "")
        message.reply_text.assert_called_once_with(
            "Too many attempts. Please wait a while and try again."
        )

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

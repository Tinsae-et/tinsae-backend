# Remediation plan — architecture & security review

Handoff document. Everything below is either **done and verified**, **done and
unverified**, or **not started**. Each item says which.

**All code and documentation work in Batches 1-3 is applied, and the full suite
passes: 341 tests, 65s, 2026-08-28.** The one thing still outstanding is not
code — it is §6.1, whether the frontend reads the `freshness` key that was
removed from the buyer payload. That is the only change here that can break
something outside this repo, and it has been open the whole time.

---

## 0. How to work in this repo

Read `CLAUDE.md` first — it is binding and it overrides default agent behaviour.
The two rules that matter most here:

- **Never run shell commands.** No Bash, Docker, `manage.py`, `git`, `pytest`,
  or migrations. Hand the human the exact command in a copyable block and wait
  for them to report the output.
- **Propose before applying.** Present diffs in chat. Do not edit files until
  told to.

Style: black, line length 88, target py312. Ruff selects `E, F, I, B, C4, UP`
and ignores `E501`. Postgres only — there is no SQLite fallback.

Findings originate from `tinsae-architecture-security-review.md` and
`architecture-and-code-review.md` at the repo root. `tests/test_review_regressions.py`
is the evidence file for those findings.

---

## 1. Domain context an agent needs before touching this

**Two clocks, deliberately separate.** This is the distinction most of the
remaining documentation work is about, so read it twice.

- `catalog/freshness.py` derives a *tier* from `last_confirmed_at` on a short
  clock (`FRESH_WINDOW` 48h, `ALMOST_EXPIRED_WINDOW` 7d). Values:
  `fresh` / `almost_expired` / `expired`.
- `Product.confirmation_status` is a *stored column* on a 30-day
  `PRODUCT_STALENESS_DAYS` clock, maintained by `sweep_freshness`. Values:
  `fresh` / `stale` / `unconfirmed`.

Shipping both to buyers let one payload say `{"confirmation_status": "fresh",
"freshness": "expired"}` about the same product, so `freshness` was removed from
the buyer payload. **The stored column is what buyers see. The derived tier is
now used for ranking only.** Any document still describing freshness as "derived
at read time" and showing buyers the `almost_expired` tier is out of date.

**`ConfirmationStatus` is a ladder, worst last** (`catalog/models.py:19-46`):
`FRESH` → `STALE` → `UNCONFIRMED`. `STALE` and `UNCONFIRMED` both mean "past the
clock"; what separates them is evidence of merchant disengagement. Anything
measuring catalogue health must count them **together** — see `ops/metrics.py`.

**`catalog.services.apply_confirmation` is the single writer of the confirmation
clock.** Nothing else may set `last_confirmed_at` or `confirmation_status`.
Both serializers mark them read-only for this reason: a merchant who could PATCH
them would keep their listings permanently fresh and hollow out the whole signal.

**Why an orphaned `ConfirmationRequest` is dangerous.** `ops/metrics.py` computes
`resolved = answered + expired` and puts `expired` in the denominator of the
response rate. Two lapses land a shop in the `never_responds` list, and the
remedy that list feeds is **unverification**. So any path that records a
non-response against a merchant who actually replied ends in a real shop being
unverified. Two separate bugs of exactly this shape have now been fixed.

**Django traps this codebase has already been bitten by:**

- Templates fail silently. An unknown variable renders as `""`, so a typo'd
  column passes every status-code assertion. This has happened twice in
  `ops/templates/ops/health.html` (`denied`, then `anexpiredswered`).
- `Count(filter=Q(status="typo"))` returns zero and raises nothing.
- `choices` on Postgres creates **no** `CHECK` constraint. Adding a value to a
  `TextChoices` produces a pure state migration with zero SQL, invisible to the
  entire test suite. Only `makemigrations --check --dry-run` notices it missing.
- `.update()` bypasses model validation entirely.
- `Decimal("nan")`, `Decimal("Infinity")`, `Decimal("1e999999")` are all valid
  literals that clear parsing and fail later and worse. See
  `catalog/views.py:price_param`.
- Postgres FK constraints are created `DEFERRABLE INITIALLY DEFERRED`, so an FK
  violation does **not** raise at `INSERT` — it surfaces at commit, which never
  happens inside a `TestCase`. Use a NOT NULL violation when a test needs a
  synchronous `IntegrityError`.

---

## 2. Batch 1 — committed

Reported committed by the human; confirm with `git log --oneline -5` if it
matters. Contents:

- `ops/templates/ops/health.html` — `{{ s.anexpiredswered }}` → `{{ s.answered }}`.
  A botched find/replace had blanked the per-shop Answered column, which is the
  number an operator reads before acting on the `never_responds` list.
- `catalog/models.py` — `ConfirmationStatus` gains `UNCONFIRMED`.
  *(Verified present, lines 19-46.)*
- `catalog/migrations/0010_alter_product_confirmation_status.py` — the state
  migration for it.
- `freshness` removed from the buyer-facing product payload.

**⚠ Unresolved:** whether migration `0010` landed *inside* that commit or is
split from its model change. A commit carrying the model change without the
migration cannot build a fresh database and fails CI's drift gate in isolation.
Worth a `git log --oneline --stat -5` to confirm.

---

## 3. Batch 2 — applied and verified, **uncommitted**

Five files. All verified present in the working tree at the line numbers given.

| File | Change | Lines |
|---|---|---|
| `catalog/services.py` | `apply_confirmation` now looks up and resolves the product's outstanding pending `ConfirmationRequest` when `request=None`, under `select_for_update`, inside the existing `transaction.atomic()` | 67-86 (docstring 61-65) |
| `bot/tests.py` | `DashboardAnswersCloseOutstandingPrompts`, two tests | 475-499 |
| `ops/tests.py` | `test_a_shop_answering_in_the_dashboard_is_not_flagged_unresponsive` | 124-143 |
| `catalog/views.py` | `freshness_rank` comment corrected — it described a two-value enum that no longer exists | 83-88 |
| `shops/serializers.py` | `ShopProductSerializer` docstring no longer names a class that doesn't exist | 78-80 |

**The bug:** a merchant answering in the dashboard left the Telegram prompt
pending. The sweep later expired it, recording a permanent bogus non-response
against a shop that had in fact replied — the `never_responds` → unverification
path described in §1.

**The discriminating test** is the one in `ops/tests.py`. Against the old code it
fails with `expired: 2`, `response_rate: 0.0`, and `Replies` sitting in
`never_responds`. The two tests in `bot/tests.py` assert the request status
directly; note that asserting the *product* ends `FRESH` would **not** have
discriminated, because sweep step 2 restores it either way.

---

## 4. Batch 3 — code and tests applied and verified, **uncommitted**

### Applied and verified in the tree

**`users/models.py:29-35`** — `Meta.ordering` changed from `["email"]` to
`["username"]`. `email` is `null=True` by design (a large share of Ethiopian
merchants have none) and Postgres gives no defined order among NULLs, so any
paginated list of users could repeat a row on one page and skip another
entirely, varying between identical requests. `username` is non-null and unique.

**`users/migrations/0003_alter_user_options.py`** — `AlterModelOptions` setting
`{"ordering": ["username"]}`. Matches the model, so the drift check should be
clean. Note its header reads *"Generated … on 2026-08-27"*, a day before the
model change, so this was almost certainly an existing untracked `0003`
overwritten in place rather than a fresh `0004`. Harmless — it was never
committed, and `AlterModelOptions` carries no SQL (model options are read off
the model at runtime, not replayed from migration state) — but worth one glance
to confirm nothing else was lost from whatever `0003` originally held.

**`core/slugs.py`** — new module. `available_slug()` (the old scan, extracted)
and `save_with_unique_slug()` (the scan plus an `IntegrityError` retry).

**`shops/models.py`** — `Shop` now inherits `BaseModel`; its duplicate
`created_at`/`updated_at` deleted; `class Meta(BaseModel.Meta)` keeping
`ordering = ["name"]`; `save()` routed through the retry.

**`catalog/models.py:160-166`** — import added, `Product.save()` routed through
the retry.

**`shops/tests.py:39-71`** — two tests in `ShopModelTest`:
`test_a_slug_taken_between_the_scan_and_the_insert_is_retried` and
`test_an_unrelated_integrity_error_is_not_retried`.

`Category` needed nothing — its slug is supplied explicitly, no `save()` override.

### Why the retry, and the one subtle part

Both models did check-then-act: scan for a free slug, then insert. Two
concurrent creates of the same name both find the slug free, both insert, and
the loser hits the unique index — an unhandled `IntegrityError`, so an ordinary
create returns 500.

**Each attempt must be its own savepoint.** Without `transaction.atomic()`
around the save, the failed `INSERT` poisons the surrounding transaction — every
Django `TestCase` and any `ATOMIC_REQUESTS` view runs inside one — and the
retry's own query raises `TransactionManagementError` instead of retrying. That
failure mode is invisible in normal use and appears only on a real collision,
which is *worse* than the 500 it replaced. This is the single most important
thing to not break, and
`test_a_slug_taken_between_the_scan_and_the_insert_is_retried` is the only thing
that pins it.

A non-slug `IntegrityError` is re-raised on attempt one rather than retried five
times, so the real error isn't buried.

### Expected migration output

`shops` should emit **nothing** — abstract inheritance touches no columns and
the autodetector matches fields by name, not declaration order. **If `shops`
emits a migration, stop and show a human:** that would mean `BaseModel`'s field
definitions are not as identical to Shop's old ones as they read.

---

## 5. Remaining work

### 5.1 Verify everything — **DONE**

Full suite run on 2026-08-28: **341 tests, 65.027s, OK.** Migration state
reported clean by the human. The `Bad Request` / `Too Many Requests` lines in the
output are the auth and throttling tests asserting their own 4xx responses, not
failures.

`shops` emitted no migration, confirming the `BaseModel` switch was the pure
no-op predicted in §4: abstract inheritance touches no columns.

To re-verify after any further change:

```bash
docker compose exec backend python manage.py makemigrations --check --dry-run
docker compose exec backend black --check . && docker compose exec backend ruff check .
docker compose exec backend python manage.py test -v 1
```

Run the full suite, not selected apps — the `ordering` change is global. Note
that a passing suite does **not** cover migration drift (§1: `choices` produces
no `CHECK` constraint, so a missing migration is invisible to every test), and
that CI runs `black --check` one step *before* the drift gate, so a Django-
generated migration that black would reformat fails first and hides the drift
result.

### 5.2 Slug retry tests — **DONE**

Applied at `shops/tests.py:39-71`. No further action.

### 5.3 `tests/test_review_regressions.py` docstrings — **DONE**

Module docstring, the `ExpectedDrift` class docstring, and both per-test
docstrings now agree: expired listings are demoted rather than hidden, and that
is a settled v1 decision rather than an open gap.

`test_pause_and_sold_out_are_indistinguishable_in_the_data` was deliberately
left alone — that one *is* still genuinely open, see §6.3.

Prose only; no assertion changed.

### 5.4 `tinsae-project-overview.md` §3 — **DONE**

The section now describes both clocks. Its opening claim no longer says stale
products stop being visible; the single "Buyer sees" table has been split into
the derived ranking tiers (`catalog/freshness.py`, ranking only) and the stored
`confirmation_status` values (the label buyers actually receive); and the closing
paragraph now names both threshold homes rather than "one module".

**Still open, out of scope for this pass:** Section 9 ("current state") carries a
provenance warning saying it had already drifted from the code as of Aug 14 and
must be verified against the repo before being trusted. Nobody has done that. It
should not be read as fact in the meantime.

---

## 6. Open decisions — these need the human, not an agent

### 6.1 Does `tinsae-frontend` read `freshness`? — **blocking, unanswered**

The highest-stakes open item, unanswered across this entire workstream. Removing
`freshness` from the buyer payload is the only change in any batch that can break
something **outside this repo**. If the frontend reads it, this must ship as a
deprecation (keep the key, mirror `confirmation_status`, remove a release later)
rather than a delete.

### 6.2 Should `UNCONFIRMED` rank below `STALE` in `freshness_rank`?

`catalog/views.py:89-94` puts never-confirmed products in tier 2 and lumps
`stale` and `unconfirmed` together in tier 1. Arguably a product that was asked
and never answered deserves to rank below one merely past the clock.

### 6.3 Availability enum

`pause` and `mark-sold` both land as `is_available=False` and differ only in
whether the clock moved. No query can separate them; the dashboard cannot render
them differently. Pinned by
`test_pause_and_sold_out_are_indistinguishable_in_the_data`.

### 6.4 `confirm-all` runs N→2N queries

Noted, not measured, not urgent.

---

## 7. Settled — do not reopen

- **Expired products are demoted, not hidden.** The code stands; the overview is
  what gets corrected (§5.4). This was previously listed as an open decision.

## 8. Deferred by explicit user decision — do not re-propose

- "Resume and confirm" dashboard button
- Availability enum (`active` / `paused` / `sold`) as an implementation
- Shop-level "closed this week"

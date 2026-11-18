# Renewals and contract end dates: design

Mockups: `.impeccable/mocks/renewals/renewals-approved.html` (the approved Option A, also published at https://claude.ai/artifact/Xieeccdu6SWXgEc9jxLfYZ).

## Intent

UK households lose money when contracts roll over unattended: insurance auto-renews at a higher price, a fixed energy tariff lapses onto a variable one, broadband goes out of contract, a mortgage fix ends onto the lender's standard variable rate. slopFi already knows most of the household's regular payments and the mortgage's fix end date, but nothing warns ahead of time. The household (two people, one joint account that pays most bills) opens slopFi mostly at month close, so the warning has to arrive at a close, early enough to shop around.

The second purpose is memory: at each renewal, record last year's price, the renewal quote, any other quote and what was chosen, so the household can see what shopping around saved and what the price did over several years.

## Goals

- A **Renewals** register: one list of everything with an end or due date the household wants to act on.
  - Kinds: insurance (car, home, pet, breakdown, life), energy, broadband, mobile, car dates (MOT, road tax, service), other.
  - The mortgage fix appears automatically from Net worth.
- A sixth **month-close step**, "Renewals checked", listing what needs a decision before the next close.
- **Recording a renewal**: renewal quote, best other quote (optional), what was done (stayed, switched, haggled), new provider, new price, new end date. Car dates record "done" with the price paid and the next due date.
- **History** per renewal, and the saving from each renewal.
- **Suggestions** from the household's direct debits that look like contracts, so most entries are a click plus an end date.
- **Demo household** gets a handful of renewals so the demo and screenshots show the feature.

## Non-goals

- Notifications of any kind (email, push). slopFi stays offline and pull-only.
- Detecting price rises from linked payments between renewals. That is a separate feature, though it will build on the payee link added here.
- Auto-detecting annual payments, or quotes from comparison sites.
- Editing the mortgage beyond what a renewal changes (fix end, rate, monthly payment, lender). Everything else stays on Net worth.

## Anti-goals

- A renewal must never block closing a month.
- No nagging: one step on the checklist and one count in the sidebar. No banners on other pages.
- The mortgage is never entered twice. Net worth stays the source of its fix end date, rate and payment.

## Approaches

- **A. Its own page in the sidebar's Review group (chosen).** Renewals are things to act on. A sidebar count keeps them visible between closes, and the page has room for the summary, groups, inline renew form and history.
- **B. A tab on Spending next to Fixed costs (rejected).** Less to build, but it hides an action list inside an analysis page, where the period filter means nothing. It would only be reachable through the Review step.
- **C. New holding kinds on Net worth (rejected).** Holdings feed net worth and the projection, and a broadband contract is neither an asset nor a liability.

## Behaviour

### Reminder window

- Each renewal has an end (or due) date and a lead time.
  - Defaults: **mortgage fix 6 months (183 days); everything else 3 months (91 days)**. Editable per renewal.
- The reminder starts at `ends_on − lead`.
- A renewal is **due** when its reminder start falls on or before `today + 35 days`. This looks ahead to roughly the next close, so a reminder never starts and slips past between two closes.
- A renewal whose end date has passed without being renewed stays due, shown as "ended N days ago".

### Page groups

The page groups renewals as:

- **Due before the next close:** the due ones above.
- **Next six months:** ends within 183 days and not due.
- **Later:** everything else.

Within each group, sorted by end date.

### Month-close step

- New step key `renewals`, title "Renewals checked", placed sixth after Targets. `STEPS` becomes 6.
- Detail line: "N renewals need a decision before the next close", listing each with its end date and two actions:
  - **Renew** (or **Done** for car dates) links to the inline form on the Renewals page.
  - **Look at it next close** defers it for the checklist's month.
- Done when every due renewal is either deferred for that month or no longer due (renewing moves its end date out of the window).
- Deferral uses the existing accept pattern (compare `accepted_spikes`). It applies to that month only, so the renewal reappears at the next month's checklist if still due.
- With no renewals at all, the step is done and reads "No renewals recorded yet", with an action linking to the page to add some. It does not nag.
- Months closed before this ships stay closed.
- **Which month the step judges** (Claude's call): "due" is measured from today, so the step is only judged for the latest open month.
  - For any other month (closed, or an older month reopened to fix something) it shows as done: "Checked at the latest open month".
  - So reopening an old month never makes you defer today's renewals against it, and a closed month keeps its "every step done" heading.

### Sidebar

"Renewals" sits in the Review group after Transactions, with a badge of due renewals not deferred for the current checklist month. It uses the same mechanism as the uncategorised count (`shell` in `web/app.py`, groups in `base.html`). It needs a new icon in `_icons.html`.

### Page

- **Header:**
  - Title "Renewals".
  - Status line: "Contracts and dates that need a decision before they roll over. N are due before the next close."
  - Primary button **Add renewal**.
- **Summary:**
  - Yearly cost across all renewals: monthly prices × 12, yearly prices × 1. One-off car dates count at their price per year.
  - Saved at renewal this calendar year.
  - Number of suggestions waiting.
- **Table columns:** Renewal (name, with provider · owner · how paid underneath), Ends (a relative pill plus the date), Paying now, Last time, action.
- **"Paying now"** (Claude's call): always the **recorded price** with its period ("a month" / "a year"). The recorded price is the one figure every calculation uses: savings, "up X%", the event's `old_price`, the summary's yearly cost. When the renewal is linked to a payee and its latest payment differs from the recorded monthly price by more than 1%, a second line reads "latest payment £Y". Nothing is recalculated from it. A suggestion or payee link prefills the recorded price from the payment once.
- **How paid** (Claude's call), worked out rather than stored:
  - from the linked payee's type code: "direct debit", "standing order" or "card";
  - otherwise from `price_period`: "paid monthly", "paid yearly" or "one payment".
  - Anything else ("SIM only", "minimum term") goes in notes, which show on the row's second line.
- **"Last time":** the outcome of the most recent renewal event:
  - "saved £X" when a renewal quote was recorded and the chosen price was lower;
  - "up X%" when the household stayed and the price rose;
  - "–" otherwise.
- **Mortgage row:** a "From Net worth" badge. It is created and kept in sync automatically (see Data).
- **Owner choices:** as on holdings (`household.owner_choices`).
- **Editing:** edit (name, kind, provider, owner, end date, price and period, lead time, payee link, notes) and delete.
  - Delete asks first and says the history goes with it, following the app's existing convention.
  - The mortgage row cannot be deleted here. It is archived if the holding or its fix end date is removed (see Data).
- **Empty state:** says what the page is for, shows suggestions if any, and offers **Add renewal**.

### Recording a renewal

**Renew** expands an inline form inside the row (no modal), following the Transactions row-swap pattern with htmx.

Fields:

- Renewal quote from the current provider (optional).
- Best other quote and its provider (optional).
- What did you do: Stayed / Switched / Haggled.
- New provider: prefilled with the current one, or with the other provider when Switched.
- New price and its period.
- New end date: defaults to the current end date + 1 year. For mortgages, + the new fix length, entered as a date.
- Mortgage only: new rate and new monthly payment.

As you type, a result line shows the saving:

- The saving is the annualised renewal quote minus the annualised new price. It is shown only when a quote was given.
- A second figure compares with the current annual price.

On **Save renewal**:

- One renewal event is stored (see Data).
- The renewal's provider, price, period and end date are updated.
- For the mortgage, the holding's `fix_end`, `rate` and `monthly_payment` are updated instead. Net worth and the projection pick them up.
- A toast confirms it and the checklist refreshes (`refresh` trigger).

Car dates use the same form reduced to: price paid, date done, next due date (defaults to + 1 year). The event is stored with outcome `done`.

**History** under the form lists past events, newest first: when, provider before → after, renewal quote, paid, saved. Events are not edited in place.

**Undoing a mistake** (Claude's call): only the newest event can be deleted (it asks first).

- Deleting it restores the term it replaced from its `old_*` columns: provider, price, period and end date.
- For the mortgage it restores the holding's `fix_end`, `rate`, `monthly_payment` and `provider`. This overwrites any Net worth edit to those four fields made since. The confirmation says so.
- Older events cannot be deleted. This keeps restore unambiguous.

**Switching a mortgage** (Claude's call): Save renewal with "Switched" also writes the new lender to the holding's `provider`, so Net worth and the next event's `old_provider` stay right. The holding fields a renewal may change are therefore `fix_end`, `rate`, `monthly_payment` and `provider`.

### Suggestions

Candidates come from `reports.recurring()`:

- Rows with `fixed` (direct debit or standing order).
- Category in Housing (except Mortgage and Mortgage overpayment), Utilities, Transport, or Pets.
- Or any category whose name contains "insurance".
- Not already linked to a renewal, and not dismissed.

Each suggestion shows the merchant, category, months seen and amount a month, with **Add** (prefills the add form: name from the category leaf, provider from the merchant, price per month, payee link) and **Dismiss** (stored, never shown again).

### Payee link

- A renewal may store `payee_key`: the merchant key `reports.recurring()` groups by (`categorise.suggest_pattern(description)`).
- "Paying now" uses the latest payment in that group.
- The link is set from a suggestion, or chosen from a list of current recurring merchants in the add/edit form. Typing a key is not required.

## Data

New tables in `db.py` SCHEMA, with a schema version bump. New tables need no migration step, as with versions 3 and 5.

**`renewals`:**

| Column | Notes |
|---|---|
| `id` | |
| `name` | text, required |
| `kind` | one of `insurance`, `energy`, `broadband`, `mobile`, `car`, `mortgage`, `other` |
| `provider` | text |
| `owner` | text, default `joint` |
| `ends_on` | ISO date, required except for the mortgage row |
| `lead_days` | integer, null means the kind's default |
| `price` | real, nullable |
| `price_period` | `month`, `year` or `once` |
| `payee_key` | text, nullable |
| `holding_id` | nullable, unique, references `holdings(id)` ON DELETE SET NULL |
| `archived_at` | nullable; set for a mortgage row whose holding was deleted or whose fix end was cleared |
| `notes` | |
| `created_at` | |

For the mortgage row (`holding_id` set), `ends_on`, `price` (monthly payment), `price_period` (`month`) and `provider` are read from the holding, not from this row.

**Mortgage sync** (Claude's call: history is kept). On every read of renewals, one function:

- ensures a row exists for each `mortgage` holding with a `fix_end`, un-archiving it if one was archived;
- **archives**, never deletes, rows whose holding has no `fix_end` or was deleted (`holding_id` NULL). Archived rows keep their events, are left out of the groups, the checklist, the sidebar count and the yearly summary, and appear in a collapsed "Archived" list at the foot of the page with their history.
- For an archived mortgage row with no holding, the provider and price shown are the newest event's `new_*` values.
- Saving a renewal on an archived row is not offered.

**`renewal_events`:**

| Column | Notes |
|---|---|
| `id` | |
| `renewal_id` | references `renewals` ON DELETE CASCADE |
| `recorded_on` | |
| `outcome` | `stayed`, `switched`, `haggled` or `done` |
| `old_provider`, `old_price`, `old_period`, `old_ends_on` | the term being replaced |
| `quote`, `quote_period` | the renewal quote, nullable |
| `other_quote`, `other_period`, `other_provider` | nullable |
| `new_provider`, `new_price`, `new_period`, `new_ends_on` | the new term |
| mortgage only: `new_rate`, `new_payment` | |

The saving is computed when read, not stored.

**`deferred_renewals`:** (`month`, `renewal_id`, `deferred_at`), primary key (`month`, `renewal_id`). Mirrors `accepted_spikes`.

**`dismissed_renewal_suggestions`:** (`payee_key` primary key, `dismissed_at`).

**Annualising:** month × 12, year × 1, once × 1.

## Code layout

- `src/slopfi/renewals.py`: register logic, the due window, mortgage sync, suggestions, recording events and the savings figures.
- `src/slopfi/web/routes/renewals.py`: the page, add/edit/delete, the renew form partial, defer and dismiss. Registered in `web/app.py`.
- `src/slopfi/web/templates/renewals.html` plus row partials.
- `review.py`: the new step, with deferral functions alongside `accept_spike`.
- `base.html`, `web/app.py` `shell`: nav entry and badge.
- `demo/`: a few renewals in the demo household, including one due, one with history, and the mortgage via its existing holding.
- Tests:
  - `tests/test_renewals.py`: due window edges, mortgage sync, event recording and savings, suggestions filter and dismissal, deferral per month.
  - Update the checklist step-key assertions in `tests/test_phase1.py`.
  - A web smoke test in `tests/test_web.py`.
- `README.md`: add Renewals to "The pages" table and mention the sixth step.

## Left to the builder

- Exact copy beyond what is quoted here, following the app's existing voice.
- Visual detail within slopFi's existing tokens and components (the mockups show intent, not pixels).
- Whether the renew form and history load as one partial or two.
- Index choices and query shapes.

# erpnext-postgres-compat-patch

Makes ERPNext v16.33.0 and v16.60.0 work on PostgreSQL.

ERPNext is developed and tested against MariaDB. It runs on PostgreSQL, but
PostgreSQL enforces parts of the SQL standard that MariaDB does not, and ERPNext
also uses MariaDB-only SQL functions in places, so a number of queries fail
outright on PostgreSQL.

Upstream has fixed most of this — carefully, with regression tests — but only on
the `develop` branch, which is `17.0.0-dev`. Almost none of it reaches the `v16`
releases: by v16.60.0 a handful of PostgreSQL fixes have been cherry-picked and a
few failing queries disappeared in unrelated refactors, but Trial Balance,
Balance Sheet, Profit and Loss and posting to the ledger still fail. This
repository carries the fixes onto two releases, **v16.33.0** and **v16.60.0**.

## The problem, concretely

Opening the **Trial Balance** report on PostgreSQL:

```
psycopg2.errors.GroupingError: column "tabGL Entry.account_currency" must appear
in the GROUP BY clause or be used in an aggregate function
```

The query selects `account_currency` as a plain column while grouping only by
`account`. MariaDB tolerates this and picks an arbitrary value; PostgreSQL
rejects it, per the SQL standard.

The failure is also hard to read: PostgreSQL aborts the whole transaction, so
Frappe's own `frappe.log_error` call then fails with `InFailedSqlTransaction`,
burying the original error under nested tracebacks.

## Why you cannot just upgrade

The fix exists upstream. It is simply not in any release:

| ERPNext version | Trial Balance fixed? |
|---|---|
| v16.20.0 … v16.33.0 (10 releases tested) | no |
| v16.60.0 | no |
| `develop` (17.0.0-dev) | yes |

Upstream's position is that PostgreSQL support is experimental and lives on
`develop`/nightly. A proposed generic framework-level fix
([frappe/frappe#40435][pr40435]) was rejected as a "magical compatibility fix";
a large PostgreSQL compatibility PR for ERPNext ([#55905][pr55905], 234 files)
was closed without merging; and [#38664][i38664] ("Full PostgreSQL Support") was
closed as *not planned*.

[pr40435]: https://github.com/frappe/frappe/pull/40435
[pr55905]: https://github.com/frappe/erpnext/pull/55905
[i38664]: https://github.com/frappe/erpnext/issues/38664

## Versions

| Patch set | ERPNext | Frappe | `bench backup` against PostgreSQL 17 | Status |
|---|---|---|---|---|
| `patches/v16.33.0/` | v16.33.0 | 16.31.0 | fails: the official image ships `pg_dump` 15 | frozen; its [known problems](#known-problems-not-fixed) are not being fixed |
| `patches/v16.60.0/` | v16.60.0 | 16.60.2 | works: the official image ships `pg_dump` 17 | current |

Each set is cut against one exact ERPNext tag; `apply.sh` refuses any other. On
PostgreSQL 17, v16.60.0 is the one to use: `bench backup` works again, and it
also covers the stock and manufacturing paths that the v16.33.0 set leaves
broken.

## Applying

Pass the version, or let `apply.sh` read it from `erpnext/__init__.py`:

```bash
cd $BENCH/apps/erpnext
/path/to/erpnext-postgres-compat-patch/apply.sh v16.60.0   # or v16.33.0
```

In a Docker build, pin to a commit rather than a branch so rebuilds stay
reproducible — this repo has no tags, and `master` moves:

```dockerfile
RUN git clone --quiet https://github.com/alfredoka/erpnext-postgres-compat-patch.git /tmp/pgpatch && \
    git -C /tmp/pgpatch checkout --quiet <commit-sha> && \
    cd apps/erpnext && /tmp/pgpatch/apply.sh v16.60.0 && \
    cd - && rm -rf /tmp/pgpatch
```

In `frappe/erpnext:v16.60.0` the bench user is uid 1000 with gid 0 and there is
no `frappe` group, so if you `COPY` the patches into the image instead, use
`--chown=1000:0`.

`apply.sh` uses `git apply`, so it fails loudly if a hunk no longer applies —
the build stops rather than silently shipping a half-patched image. After an
ERPNext upgrade, regenerate the patches rather than forcing them.

These are source-level changes only; the patch adds no migrations of its own.
The v16.60.0 set does fix one of ERPNext's own migration patches, so apply it
**before** running `bench migrate` (see [Upgrading from
v16.33.0](#upgrading-from-v16330)).

## How the fixes work

The changes cover several distinct MariaDB-vs-PostgreSQL differences:

- **Non-grouped columns in an aggregate SELECT** — wrapped in `Max()` when the
  column is constant within each group, which keeps the grouping key unchanged
  and returns the same value MariaDB picked arbitrarily. Preferred over widening
  the `GROUP BY`, which would change the grouping key and could split rows and
  sums. When several columns must come from the *same* row (a line's `uom` and
  its `conversion_factor`, say), independent `Max()`es could stitch together a
  combination that exists on no row; there `develop` either adds the correlated
  columns to the `GROUP BY` or reads them from one representative row per group
  (`Min(name)` or `Min(idx)`), and this patch does the same. The exception is
  noted under [v16.60.0 known limitations](#known-limitations).
- **Functional dependency across joins** — PostgreSQL recognises it only from
  the grouped table's own primary key, never through a join equality, so the
  other table's key is added to the `GROUP BY`.
- **SELECT aliases in `HAVING`** — not allowed on PostgreSQL; the aggregate
  expression is bound to a variable and repeated.
- **ORDER BY on a non-grouped column** in an aggregate query — wrapped in
  `Min()`, which preserves the original ordering where the column is unique.
- **MariaDB-only SQL** — `DATEDIFF`, `TO_SECONDS`, `MONTHNAME`, `YEAR()`/
  `MONTH()`/`QUARTER()`, `IF()`, `IFNULL()`, `GROUP_CONCAT ... SEPARATOR`,
  `TIMESTAMP(date, time)`, `DATE_SUB(... INTERVAL ...)`, `REGEXP`,
  `UNSIGNED` casts, `CURRENT_DATE()` as a call, `LIMIT offset, count`,
  `ORDER BY NULL`, single-quoted column aliases, `UPDATE ... JOIN`, and
  `'0000-00-00'` as a date.
- **MariaDB-only index hints** — `FORCE INDEX (...)`, which PostgreSQL has no
  equivalent for (its planner picks the index on its own); guarded on
  `frappe.db.db_type` and dropped entirely on PostgreSQL.
- **`FOR UPDATE` together with `GROUP BY`** — rejected by PostgreSQL; as on
  `develop`, the lock is kept on MariaDB and, where it matters, PostgreSQL locks
  the underlying rows with a plain `SELECT ... FOR UPDATE` first.
- **Default ORDER BY next to an aggregate** — `frappe.db.get_value()` with an
  aggregate field (`[{"MAX": ...}]`) still appends `ORDER BY creation`, which
  PostgreSQL rejects; `order_by=None` is passed, as Frappe does in its own
  internal lookups. The result is a single row, so the order never mattered.
- **Booleans written into Check columns** — Check fields are `smallint` on
  PostgreSQL, and a Python `True` written through `set_value` or
  `qb.update().set()` becomes the literal `true`, which PostgreSQL will not
  assign to a smallint; `1` is written instead. A smallint used directly as a
  condition (`OR t.is_x`) gets an explicit `= 1`.
- **Division by zero** — MariaDB returns `NULL`, PostgreSQL raises; the divisor
  is wrapped in `NULLIF(..., 0)`, which gives the same `NULL` on both.
- **Collation-dependent ordering** — explicit `casefold` sorting so column order
  is identical on both backends.

Changes carry a comment explaining why they are safe where the reason is not
obvious from the code.

### Two kinds of change, and why it matters

Most hunks are **backported verbatim** from `develop`: same code, same comments,
verified to reproduce upstream's state exactly. Where that was not possible, the
fix is **written by hand** against the release's own code; each version's
section below lists those files and why.

**The Frappe 17 trap.** Frappe `develop` adds PostgreSQL-aware helpers that
Frappe 16 does not have — neither 16.31.0 nor 16.60.2:

- `DateDiff` — mapped per engine on 17; on 16 it resolves to pypika's, which
  renders `DATEDIFF(...)` (MariaDB-only) and takes three arguments, not two, so
  `develop`'s two-argument call raises `TypeError` on *both* engines.
- `CurDate` — a `Term` rendering the bare `CURRENT_DATE` keyword on 17; on 16 it
  resolves to pypika's, which renders `CURRENT_DATE()`, invalid on PostgreSQL.
- `Abs` — a plain `Function` on 17; on 16 it is pypika's `AggregateFunction`, so
  a scalar `ABS(...)` in a SELECT makes Frappe treat the whole query as an
  aggregate, which on PostgreSQL wraps the default ORDER BY in `MAX()`.

Copying `develop`'s code for these files would import names that exist but
behave differently — **failing silently at runtime rather than at import**. The
hand-written versions branch on `frappe.db.db_type` and use only what Frappe 16
actually ships. More of the same on Frappe 16.60.2:

- `MonthName`, `Year`, `Month` and `Quarter` in `frappe.query_builder.custom`
  still render the MySQL functions (only Frappe `develop` translates them);
- `GroupConcat(col, ", ")`, as `develop` writes it, passes the separator as the
  alias on MariaDB and raises `TypeError`;
- `PostgresDatabase.add_index` takes no `where` or `include`, and there is no
  `frappe.db.transaction_advisory_lock`;
- `frappe.get_all(..., group_by=...)` or `distinct=True` silently drops the
  `ORDER BY` on PostgreSQL (no error, only a different order).

## v16.60.0

### Why a second version

`bench backup` on PostgreSQL runs `pg_dump`, which refuses to dump a server
newer than itself. The official `frappe/erpnext:v16.33.0` image ships `pg_dump`
15, so against PostgreSQL 17 `bench backup` fails. `frappe/erpnext:v16.60.0`
(Frappe 16.60.2, Debian trixie base) ships `pg_dump` 17: `bench backup
--with-files` and `bench restore` work against PostgreSQL 17 as shipped.

Unpatched, v16.60.0 is still unusable on PostgreSQL: Trial Balance fails
(`GroupingError`), Balance Sheet and Profit and Loss fail (`FORCE INDEX`), no
Journal Entry can be submitted and no Period Closing Voucher cancelled
(`validate_against_pcv`), and `bench migrate` from v16.33.0 stops halfway on one
of ERPNext's own migration patches.

### What is covered

82 files, 251 hunks, in 16 patches, one per top-level ERPNext
module (the v16.33.0 set needed 7):

| Patch | Area |
|---|---|
| `01-accounts.patch` | accounting reports, ledger posting, period closing, payments, bank tools |
| `02-buying.patch` | Supplier Scorecard, Requested Items to Order and Receive |
| `03-selling.patch` | quotation expiry, sales reports |
| `04-stock.patch` | stock ledger, serial/batch valuation, reservations, pick list, stock reports |
| `05-manufacturing.patch` | BOM, Production Plan, Work Order, Job Card, reports |
| `06-erpnext_integrations.patch` | Plaid savepoints |
| `07-www.patch` | support portal |
| `08-assets.patch` | daily depreciation job |
| `09-controllers.patch` | shared document controllers and link-field searches |
| `10-crm.patch` | Sales Pipeline Analytics, Lost Opportunity |
| `11-patches.patch` | three ERPNext migration patches |
| `12-projects.patch` | timesheets |
| `13-regional.patch` | UAE VAT 201 |
| `14-setup.patch` | company history, Email Digest, Sales Person |
| `15-support.patch` | Support Hour Distribution |
| `16-utilities.patch` | item-variant attribute lookup, activation level |

By area:

- **Accounting reports.** Trial Balance, Balance Sheet, Profit and Loss and Cash
  Flow (the shared `financial_statements.get_accounting_entries`: `Max()` on the
  non-grouped columns, and `FORCE INDEX` now emitted only when the engine is
  MariaDB, as on `develop`, since Frappe 16.60.2 also ships SQLite). Also General
  Ledger with *Show Remarks*, Accounts Receivable/Payable, Sales and Purchase
  Register, Item-wise Purchase Register, Budget Variance, Deferred Revenue and
  Expense, Asset Depreciations and Balances, Sales Payment Summary and Inactive
  Sales Items.
- **Ledger posting and period closing.** `general_ledger.validate_against_pcv`
  still needs `order_by=None`: Frappe 16.60.2 still appends `ORDER BY creation`
  to that `get_value`, and without the fix no document can post to the ledger.
  Also the partial-cancel path of `make_reverse_gl_entries` (a boolean into
  `is_cancelled`), the per-date summary of the new (non-legacy) period-closing
  controller, the same `get_value` pattern in Stock and Account Value
  Comparison's closed-period check (new in v16.60.0), and the Payment Ledger and
  held-invoice helpers in `accounts/utils.py`.
- **The migration patch.** `erpnext.patches.v16_0.set_outstanding_for_zero_portion_payment_terms`,
  new in v16.60.0's `patches.txt`, builds `UPDATE "tabPayment Schedule" JOIN ...`,
  which is MySQL syntax: on PostgreSQL, `bench migrate` stops there and leaves
  the database half-migrated. The fix branches on the engine: PostgreSQL gets
  `UPDATE ... SET ... FROM ... WHERE` — the approach upstream already uses in its
  own `update_serial_batch_entries` patch (frappe/erpnext#59076) — and MariaDB
  keeps byte-identical SQL. The file is the same, unfixed, on `develop`. The other
  new `patches.txt` entries were each read, and the ones that build SQL with the
  query builder were run against a real PostgreSQL (with seeded rows where the
  path depends on data); none fails on its own SQL. One,
  `recalculate_bins_for_production_plan_items`, reaches
  `stock_balance.get_reserved_qty` (fixed under stock), and only on a site with
  open production plans.
- **The SLA link-filter backfill patches.**
  `erpnext.patches.v15_0.backfill_sla_link_filters_on_custom_field` and
  `..._on_docfield` filter `"link_filters": ("is", "not set")`; `link_filters` is a
  JSON column, and Frappe 16.60's query builder renders that as
  `("link_filters" IS NULL OR "link_filters"='')`, which PostgreSQL rejects
  (`operator does not exist: json = unknown`), so `bench migrate` stops there
  (frappe/erpnext#58547). A site migrated earlier with `--skip-failing` has them
  as skipped, and Frappe retries skipped patches on the next migrate. The fix is
  upstream's own, byte for byte (develop `bee358ea2`, frappe/erpnext#59018, not
  backported to `version-16`): read `link_filters` with the other fields and skip
  the rows that already have one. Frappe `develop` also fixes the general case by
  casting JSON columns to text in filters (`frappe/database/query.py`).
- **Documents and controllers.** Payment Entry (`before_save` with references,
  *Get Outstanding Invoices*, orders to be billed), credit and debit notes and
  delivery returns, advance totals, the daily invoice-status job, landed-cost
  amounts in Purchase Receipt/Invoice validation, POS Closing Entry submit,
  Unreconcile Payment submit, Process Payment Reconciliation, the Item link
  search used by every buying and selling form, batch and dimension (Cost
  Center, Project) searches, renaming an Item Attribute value, subcontracting
  delivery, and the daily asset-depreciation job.
- **Stock.** The v16.33.0 stock hunks redone on v16.60.0 (serial/batch
  valuation, deprecated serial/batch valuation, stock reservation, pick list,
  stock ageing, items to repost), plus `REGEXP` in Stock Ledger Entry and Serial
  and Batch Bundle, reserved quantity in `stock_balance`, Repost Item Valuation
  de-duplication, Stock Reconciliation item fetch, by-products from Job Cards in
  manufacturing Stock Entries, Packing Slip, Stock Closing Entry, and the Stock
  Projected Qty, Batch-wise Balance History, Total Stock Summary, Available
  Batch, Product Bundle Balance and Stock Ledger reports.
- **Manufacturing.** `bom.get_bom_items_as_dict` (unqualified `GROUP BY` names
  that are ambiguous on PostgreSQL, a smallint used as a boolean, non-grouped
  columns) and a division by zero in `get_valuation_rate`; three Production Plan
  queries; BOM Stock Analysis, Process Loss Report, and Work Order and Job Card
  aggregates.
- **Selling and buying.** Sales Order Analysis and Inactive Customers
  (`DATEDIFF`), Payment Terms Status for Sales Order, Sales Analytics, quotation
  expiry, Supplier Scorecard (`DATEDIFF`), Requested Items to Order and Receive.
- **Other reports and modules.** Sales Pipeline Analytics, Lost Opportunity,
  Daily Timesheet Summary, the timesheet portal list, company transaction
  history, the activation level, Email Digest, Sales Person dashboard, Support
  Hour Distribution, the support portal, UAE VAT 201, Plaid savepoints, and the
  item-variant attribute lookup (a float compared with a varchar column).

Many of these were already broken on v16.33.0 and are not covered by that set;
the v16.60.0 set fixes them rather than limiting itself to code new in v16.60.0.

### Dropped from the v16.33.0 set

- **Fixed upstream by v16.60.0**, so not patched any more: Voucher-wise Balance,
  Procurement Tracker and Purchase Order Analysis (upstream removed or corrected
  the failing `GROUP BY`), most of Sales Order Analysis (only `DATEDIFF` was
  left), part of BOM Stock Analysis, the Plaid transaction-sync savepoint, and
  the `update_qty_in_future_sle` hunk (v16.60.0 already builds that `UPDATE`
  correctly).
- **Removed on purpose:** the partial indexes (`add_index(..., where=...,
  include=...)`) on GL Entry, Stock Ledger Entry and Serial and Batch Bundle, the
  advisory lock in batch valuation, and two unused repost "gate" helpers. All
  depend on Frappe `develop` APIs that 16.60.2 does not have, and none is a
  compatibility fix: the indexes were a performance addition, and they are not
  recreated. With them, `bench migrate` from v16.33.0 would stop with
  `TypeError` (the three doctypes' JSON changes between the two releases, so
  their `on_doctype_update` runs).
- **Sales Payment Summary:** the v16.33.0 rewrite of the filter builder is not
  carried over, because v16.60.0 added a permission restriction to that builder;
  the PostgreSQL fix is redone on top of v16.60.0's own filter code.

### Hand-written, and why

| File | Why `develop`'s code was not copied |
|---|---|
| `accounts/report/budget_variance_report` | Frappe 16.60.2's `MonthName` renders `MONTHNAME()`; a local helper renders `to_char(date, 'FMMonth')` on PostgreSQL, which is what Frappe `develop` does |
| `accounts/report/inactive_sales_items`, `selling/report/inactive_customers`, `selling/report/sales_order_analysis`, `buying/doctype/supplier_scorecard_variable` | `develop` calls Frappe 17's `DateDiff`; PostgreSQL gets `CAST(a AS date) - CAST(b AS date)`, which is what that helper renders there |
| `crm/report/sales_pipeline_analytics` | `Year`/`Month`/`Quarter` render MySQL functions on 16.60.2; replaced with `EXTRACT`, as v16.60.0 itself does in project costing |
| `crm/report/lost_opportunity` | `develop`'s `GroupConcat(col, ", ")` breaks MariaDB on 16.60.2; one branch per engine |
| `stock/doctype/stock_ledger_entry`, `stock/doctype/serial_and_batch_bundle` | `REGEXP` is unfixed on `develop` too; PostgreSQL gets `~` |
| `patches/v16_0/set_outstanding_for_zero_portion_payment_terms` | identical and unfixed on `develop` |
| `controllers/queries` (`item_query`) | `develop` rewrote the function, also changing permissions and ordering; this is a minimal fix of the raw SQL |
| `stock/stock_balance` | `develop`'s rewrite also changes what counts as reserved; this is a minimal `CASE` + `NULLIF` |
| `manufacturing/doctype/bom`, `manufacturing/doctype/production_plan` | `develop` restructured both into query-builder services and, for Production Plan, changed the grouping; v16.60.0's SQL is kept with `max()`, and `bom_no, is_phantom_item` grouped as a pair where the recursion depends on it (see [known limitations](#known-limitations)) |
| some twenty aggregate `get_value`/`get_all` calls | the calls are the same on `develop`, but its Frappe no longer adds the default `ORDER BY`; 16.60.2 still does |

### How it was verified

Static, on a clean v16.60.0 checkout:

- all 16 patches apply with strict `git apply` (no fuzz), and the result is
  byte-identical to the curated tree;
- every patched file compiles, and `tools/check_undefined_names.py` reports no
  name that the patch introduces;
- `tools/audit_groupby.py`, run as each file was fixed, leaves only its known
  false positives in patched files;
- where a fix branches on the engine, the MariaDB SQL was rendered with Frappe
  16.60.2's query builder and compared with the original; where compared, it is
  byte-identical.

On a local Docker stack — the official `frappe/erpnext:v16.60.0` image plus
`apply.sh v16.60.0`, PostgreSQL 17 — starting from a copy of a v16.33.0 site
(patched with the v16.33.0 set) that holds a full year of accounting and a
submitted Period Closing Voucher (legacy controller):

- **`bench migrate` to v16.60.0 without `--skip-failing`** completes: 118
  migration patches, about 5 minutes, no Error Log entries. The test has to
  start from the v16.33.0 database: `on_doctype_update` runs only when a
  doctype's JSON is re-imported, so a site that is already on v16.60.0 hides
  errors there. That is how the partial-index hunks would have broken a real
  upgrade.
- **Accounting matches to the cent.** The same independent verification as on
  the v16.33.0 site — ledger totals per account, and the Trial Balance (with and
  without closing entries), Balance Sheet and Profit and Loss output — gives the
  same figures; only version strings differ. The three reports return the same
  for a user with the Accounts User and Accounts Manager roles as for
  Administrator.
- **Writes, all rolled back:** a Journal Entry in the next year submits; one in
  the closed year is rejected ("Books have been closed"); the Period Closing
  Voucher cancels, and a new one completes with the same number of GL entries,
  the same total and the same number of Account Closing Balance rows as the
  original.
- **Other paths:** the Item link search, account and Cost Center searches,
  General Ledger with *Show Remarks*, and `bench backup`.
- **UI**, in a headless browser: Trial Balance, Balance Sheet, Profit and Loss,
  General Ledger, Journal Entry, Period Closing Voucher and the chart of
  accounts open without errors.
- **All 179 script reports**, run with one generic set of filters: 136 ran
  clean, 5 failed in SQL, and 38 stopped on Python-side validation of filters
  that did not fit them (those 38 were not exercised further). Of the 5, four
  come from the generic filters and run clean with proper ones (Delayed Item,
  Delayed Order, Production Planning, Incorrect Serial and Batch Bundle); the
  last is Financial Ratios, an upstream bug (below). An earlier pass had found
  three more, PostgreSQL bugs since fixed (Daily Timesheet Summary, Lost
  Opportunity, Stock Projected Qty).

Fixes in BOM and Production Plan were run as direct calls with seeded rows and
rollback, and checked against expected results; stock, manufacturing,
selling and buying flows were otherwise not exercised end to end. As with
v16.33.0, this is one deployment's data shape (accounting only), not a test
suite.

### Upgrading from v16.33.0

- Build the v16.60.0 image with the patch applied **before** `bench migrate`:
  unpatched, the migration stops halfway (see the migration patch above).
- Upstream changed period closing in v16.60.0 (this is not the patch): in the
  legacy controller, a failure while posting the closing entries now propagates
  out of `submit()`, where v16.33.0 caught it, rolled back and marked the voucher
  "Failed"; `check_if_previous_year_closed` became
  `check_if_previous_period_closed`; and the closing account must be a
  non-group, enabled, non-frozen account. Scripts that relied on the old
  behaviour need review.
- The desk UI is new: routes live under `/desk/...` (old `/app/...` links
  redirect).

### Known limitations

- **Per-column `max()` in BOM and Production Plan.**
  `bom.get_bom_items_as_dict` (as in the v16.33.0 set) and three Production Plan
  queries (`get_exploded_items`, `get_subitems`,
  `get_raw_materials_of_sub_assembly_items`) wrap every non-grouped column in
  its own `max()`. When a BOM lists the same item on several lines of one group,
  those columns can come from different lines — one line's `uom` with another's
  `conversion_factor`, say — and `max()` over text
  follows each engine's collation, so MariaDB and PostgreSQL can pick different
  values. Quantities are sums and are not affected. In BOM and in the
  Production Plan sub-assembly query, `bom_no` and `is_phantom_item` stay paired
  because they are part of the `GROUP BY` (as on `develop`); in `get_subitems`
  they are still independent, where v16.60.0 merges the rows by item anyway. Both
  files carry a `KNOWN LIMITATION` comment, and the choice is pending a closer
  review (`develop` reads those columns from a representative line instead).
- **"Today" is the app's date.** On PostgreSQL, the day counts in Inactive
  Sales Items, Inactive Customers and Sales Order Analysis, and the held-invoice
  check behind *Get Outstanding Invoices* (`accounts/utils.get_held_invoices`,
  on both engines), compare against `nowdate()` passed as a parameter, not the
  database server's `CURRENT_DATE`. Frappe stores dates in the system time zone
  but does not set the PostgreSQL session time zone, so on a server running in
  UTC `CURRENT_DATE` is a day ahead of the app late in the evening west of UTC.
  `nowdate()` matches the rest of ERPNext; the boundary hours have only been
  simulated, not tested.
- **`payment_entry.get_negative_outstanding_invoices`** keeps upstream's
  `release_date <= CURRENT_DATE`: it is valid on PostgreSQL, so it is not
  patched, but it is the complement of `get_held_invoices` and the two can
  disagree in that late-evening window. `develop` passes today's date as a
  parameter there.
- **Financial Ratios** fails when the company has no leaf account of type
  Receivable, Payable or Stock: `accounts/utils.get_balance_on(account_type=...)`
  then builds `account in ()`, a syntax error on MariaDB too. It is an upstream
  bug, unchanged on `develop`, and not touched here.
- **Where MariaDB results change.** The rule is "same result on MariaDB", with
  these exceptions:
  - `get_held_invoices` uses the app's date on both engines (above);
  - BOM and Production Plan: per-column `max()` instead of one arbitrary row's
    values, in groups where an item repeats; the `GROUP BY` on
    `bom_no, is_phantom_item` in BOM and in the Production Plan sub-assembly
    query can also split such a group, as `develop` does;
  - where MariaDB returned an arbitrary row's value, some queries now return a
    defined one (first line by `idx`, oldest invoice): Sales Payment Summary, BOM
    Stock Analysis, by-products from Job Cards; Sales Payment Summary also no
    longer merges Journal Entry rows of different voucher types under one label
    (totals unchanged);
  - `utilities/product.get_item_codes_by_attributes` compares attribute values
    as text, as `develop` does: it matches fewer rows for numbers written
    differently (`2` no longer matches `'2.0'`), while its only ERPNext caller,
    `find_variant`, returns the same;
  - the daily invoice-status job takes `develop`'s fix of an operator-precedence
    bug (`is_pos & due_date < today`), which can change which invoices it
    updates;
  - the Cost Center/Project link search no longer applies `LIKE` to Check
    columns, so searching for `0` or `1` no longer matches `is_group`;
  - Purchase Register adds up a tax account that appears as both "Add" and
    "Deduct" on one invoice instead of keeping the last row (from `develop`; the
    v16.33.0 set does the same).
- **Not run against a database**, only checked by reading the code and rendering
  the SQL: `REGEXP` as `~` (Stock Ledger Entry, Serial and Batch Bundle), Budget
  Variance, Sales Payment Summary in both POS and non-POS modes, the Payment
  Entry paths, Repost Item Valuation de-duplication, quotation expiry, the
  timesheet portal list, Supplier Scorecard, Sales Pipeline Analytics, Email
  Digest, *Create GL Reposting Entries*, Exchange Rate Revaluation, the daily
  invoice-status job, Stock Ageing with batches, and the new (non-legacy)
  period-closing controller.
- **Unrelated to PostgreSQL:** on a site without a default company or a Fiscal
  Year covering today, two standard charts on the new v16.60.0 Home workspace
  show errors — "Bank Balance" ("Invalid expression set in filter account") and
  "Profit and Loss" ("Start Year and End Year are mandatory"). It happens the
  same on unpatched v16.60.0 and on MariaDB; setting a Default Company and a
  current Fiscal Year fixes it. The Company page's goals chart (`sum(unknown)`)
  fails inside Frappe, as it already did on v16.33.0. And on the first desk load
  after the upgrade, two concurrent requests can race in Frappe's change-log
  update (`update_last_known_versions`) and one fails once with PostgreSQL's
  `SerializationFailure`; a reload works.

## v16.33.0

### What is fixed

44 files, 148 hunks, grouped by module so a single failing hunk after a future
upgrade tells you exactly what broke:

| Patch | Files | Hunks |
|---|---|---|
| `01-accounts.patch` | 19 | 63 |
| `02-buying.patch` | 3 | 8 |
| `03-selling.patch` | 4 | 11 |
| `04-stock.patch` | 13 | 51 |
| `05-manufacturing.patch` | 3 | 10 |
| `06-erpnext_integrations.patch` | 1 | 3 |
| `07-www.patch` | 1 | 2 |

`01-accounts.patch` is the one most deployments need: Trial Balance, Accounts
Receivable, Sales and Purchase Register, bank clearance and reconciliation, plus
the `gl_entry.py`, `general_ledger.py` and `accounts/utils.py` runtime paths.
Without `general_ledger.py`, submitting any document that posts to the ledger
(Journal Entry, invoices, payments) fails on PostgreSQL.

### Hand-written files

Nine files could not be backported and were **written by hand** against
v16.33.0's own code:

| File | Why it could not be backported |
|---|---|
| `accounts/general_ledger` | not fixed upstream: ERPNext `develop` makes the same call in `validate_against_pcv`, and Frappe's `version-16` branch (16.54) still adds the default ORDER BY next to `MAX()` |
| `accounts/report/budget_variance_report` | fix is entangled with a `CustomFunction` → `MonthName` refactor |
| `accounts/report/inactive_sales_items` | upstream fix relies on Frappe 17 helpers (see the trap above) |
| `selling/report/inactive_customers` | same |
| `selling/report/sales_order_analysis` | same, plus `TO_SECONDS` and a raw-SQL rewrite |
| `manufacturing/doctype/bom` | develop rewrote the raw SQL into query-builder helpers that do not exist here |
| `manufacturing/report/bom_stock_analysis` | develop changed the grouping key, which changes results |
| `stock/doctype/pick_list` | develop restructured the whole module |
| `stock/report/stock_ageing` | upstream fix relies on a Frappe 17 helper |

One file, `selling/doctype/customer`, is deliberately **not** patched: v16.33.0
already has a correct PostgreSQL branch there, and `develop`'s change to it is a
refactor, not a fix.

Four more files exist only on `develop` as part of a larger refactor
(`production_plan/services/`, `work_order/mapper.py`) and have no v16 counterpart.

### Verifying

The patch set was validated by applying it to a clean `v16.33.0` clone and
checking that:

- all 7 patches apply with no fuzz, and the result is byte-identical to the
  curated tree;
- all 44 files compile;
- no new undefined names appear versus unpatched v16.33.0
  (`tools/check_undefined_names.py <erpnext-app-dir> <file paths relative to
  it>` — pass the app directory first: given a file path alone, it checks
  nothing and still reports 0);
- no `SELECT` column remains ungrouped and unaggregated
  (`tools/audit_groupby.py`);
- no file imports a Frappe 17-only helper.

`tools/audit_groupby.py` pairs each `.select()` with the `.groupby()` of its own
query-builder chain, and knows the two rules that otherwise drown the output in
false positives: primary-key functional dependency, and aggregates bound to a
variable on an earlier line. It has two known blind spots: a `GROUP BY` built
from an interpolated variable in raw SQL, as `bom.py` does, so raw SQL still
needs reading; and any MariaDB-only SQL that isn't `GROUP BY`-shaped at all —
`FORCE INDEX (...)` in `financial_statements.py` slipped past every static
check this way, since it's a syntax error, not an aggregation mismatch, and
was only caught by exercising the actual report against Postgres.

Static checks alone cannot prove the SQL is correct, only that it is
plausible — this patch set has also been exercised against a populated
production Postgres database (all four financial-statement-family reports:
Trial Balance, Balance Sheet, Profit and Loss, Cash Flow), which is what
caught the `financial_statements.py` gap above. That is real-world
verification on one deployment's data shape, though, not a substitute for a
proper test suite against a range of data.

The period-closing path was exercised separately, on a fresh v16.33.0 site on
PostgreSQL 17: journal entries in year N, a submitted Period Closing Voucher for
N (legacy controller), then Trial Balance, Balance Sheet, Profit and Loss and
General Ledger for N and N+1, with N+1 opening balances matching the Account
Closing Balance rows and the general ledger. That run is what caught the
`general_ledger.py` gap: no journal entry could be submitted at all.

### Known problems (not fixed)

The v16.33.0 set is frozen: these will not be fixed there (the production site
it was built for only uses accounting), and the v16.60.0 set covers all of them.
They were found while porting to v16.60.0, mostly by reading the code and
rendering the SQL; only the first was seen on a running v16.33.0 site.

- **The partial indexes added by this patch break `bench new-site`.** The
  v16.33.0 set adds `frappe.db.add_index(..., where="is_cancelled = 0",
  include=[...])` calls to `on_doctype_update` in GL Entry, Stock Ledger Entry
  and Serial and Batch Bundle — a performance addition copied from `develop`.
  ERPNext v16.33.0 itself makes no such call, and Frappe 16.31.0's
  `PostgresDatabase.add_index` takes no `where` or `include`, so the call raises
  `TypeError` wherever `on_doctype_update` runs: `bench new-site` with the
  patched code, and any `bench migrate` that re-imports one of those doctypes
  (Frappe does that when the doctype's JSON has changed, for instance when an
  older site is upgraded onto patched v16.33.0). Existing sites where neither
  happens are unaffected; the indexes are performance-only and were probably
  never created there. (Earlier versions of this README blamed ERPNext for this;
  the calls come from the patch.)
- **Stock movements fail.** The `stock_ledger.update_qty_in_future_sle` hunk
  adds a second `SET qty_after_transaction = ...` without removing the first.
  PostgreSQL rejects the double assignment, and the function runs for every
  Stock Ledger Entry that moves quantity; on MariaDB it would apply the shift
  twice.
- **Outgoing stock of batched items fails**: `serial_batch_bundle.calculate_avg_rate`
  calls `frappe.db.transaction_advisory_lock`, which Frappe 16.31.0 does not
  have (`AttributeError`).
- `deprecated_serial_batch` still combines `FOR UPDATE` with `GROUP BY` in two
  queries; that fix was left half-done.
- **BOM explosion fails** (`bom.get_bom_items_as_dict`, used by Work Orders,
  manufacturing Stock Entries and Job Cards): unqualified `GROUP BY` names are
  ambiguous on PostgreSQL, and `is_phantom_item` (a smallint) is used as a
  boolean in `OR`. Confirmed by running the patched query on PostgreSQL (on the
  v16.60.0 schema; neither error depends on it). By analysis, not by a run, the
  patched query also fails on MariaDB ("Can't group on 'stock_uom'"), since the
  unqualified `stock_uom` now resolves to an aggregate alias.
- Also broken on v16.33.0 with this set: Budget Variance (`MONTHNAME`), Supplier
  Scorecard (`DATEDIFF`), reserved quantity in `stock_balance` (`if()`),
  Payment Entry `before_save` with references, credit/debit notes and returns,
  and the Item link search — most of what the v16.60.0 section lists under
  documents, stock and manufacturing was already in v16.33.0.

## Provenance and license

Most patch content is derived from [frappe/erpnext][erpnext] `develop`. ERPNext
is GPL-3.0, so this derivative work is GPL-3.0 too. Credit for those fixes
belongs to the ERPNext maintainers and contributors; this repository extracts and
re-targets them, and adds hand-written equivalents where the upstream fix could
not be carried over.

Built against ERPNext v16.33.0 with Frappe 16.31.0, and ERPNext v16.60.0 with
Frappe 16.60.2, on PostgreSQL 17.

[erpnext]: https://github.com/frappe/erpnext

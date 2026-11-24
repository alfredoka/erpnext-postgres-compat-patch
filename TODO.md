# Pending work

Tracked here until this repo is pushed to GitHub and these become issues.

## 1. Discovery method was incomplete — re-audit needed [DONE 2026-08-29]

The file list for this patch set was built by grepping `develop` for files
containing a "postgres" comment. That missed every site upstream fixed
**without** leaving a comment. Comparing the patched tree against `develop`
with `tools/audit_groupby.py` (default-based detection, not comment-based)
originally showed the gap:

```
v16.33.0 unpatched:  102 candidates
v16.33.0 patched:     51   <- should be ~4, matching develop
develop:               4
```

The 10 in-scope files were re-audited, one by one, against `develop`
(cloned to compare exact upstream technique per file):

- `buying/report/procurement_tracker/procurement_tracker.py` — fixed: real
  bug, upstream uses a representative-row subquery (`Min(name)` per group),
  not per-column `Max()`.
- `accounts/report/purchase_register/purchase_register.py` — fixed: real
  bug, several raw-SQL sites modernized to `frappe.get_all`/`frappe.qb`
  matching upstream; now byte-identical to develop.
- `accounts/report/sales_payment_summary/sales_payment_summary.py` — fixed:
  real bug, representative-row subquery pattern (same family as
  procurement_tracker).
- `accounts/report/trial_balance/trial_balance.py` — fixed: a second raw-SQL
  site (`select ... from tabAccount`) replaced with `frappe.get_all`,
  matching develop; the original first site was already correct.
- `accounts/report/item_wise_purchase_register/item_wise_purchase_register.py`
  — the audit hit here was a false positive (regex paired two unrelated
  queries), but two raw-SQL calls were still modernized to `frappe.get_all`
  to match upstream for consistency.
- `accounts/report/sales_register/sales_register.py` — false positive,
  confirmed no fix needed (audit regex paired unrelated queries; the file's
  actual `GROUP BY`s were already correct).
- `accounts/report/accounts_receivable/accounts_receivable.py` — false
  positive, file already matched develop at the real groupby site.
- `accounts/utils.py` — false positive, no unsafe `.groupby()` present.
- `controllers/accounts_controller.py` — false positive (the audit tool has
  a bug: it doesn't check `a.id in grps` for bare-name `select()` args).
- `controllers/stock_controller.py` — false positive (regex crossed a
  ~900-line span between two unrelated queries).

Verified: patches now apply clean to a fresh v16.33.0 checkout and produce a
tree byte-identical to the hand-fixed working copy; `py_compile` and
`tools/check_undefined_names.py` pass on all six touched files; re-running
`tools/audit_groupby.py` shows zero remaining candidates among the 10
in-scope files.

Out of scope (manufacturing/stock, not used here) but real: `production_plan`
(18 sites), `stock_entry` (4), `work_order`, plus several more one-offs —
also `patches/v13_0/*`, `setup/doctype/company/company.py`,
`assets/doctype/asset_maintenance/asset_maintenance.py`,
`projects/report/project_wise_stock_tracking/*`,
`selling/report/pending_so_items_for_purchase_request/*`,
`selling/report/available_stock_for_packing_items/*`,
`accounts/doctype/loyalty_point_entry/loyalty_point_entry.py` (some of these
may also be audit-tool false positives, not re-checked since out of scope).

v16.60.0 (item 6): these areas were re-audited for the new set. `production_plan`
and `stock_entry` are fixed there; the `loyalty_point_entry.py` and
`company.py` hits turned out to be audit false positives. None of it is
back-ported to the v16.33.0 set (item 12).

## 2. `manufacturing/doctype/bom/bom.py` needs to be redone [DONE 2026-08-29]

The hand-written fix wrapped ~15 non-grouped columns in independent `max()`
calls, which is unsafe: `_add_bom_item_to_dict` recurses into `bom_no` when
`is_phantom_item` is set, so independent `Max()` per column could pair one
line's phantom flag with another line's `bom_no` and explode the wrong
sub-BOM.

Turns out upstream's real fix is simpler than a representative-row subquery:
`_add_normal_item_columns` just adds `bom_no` and `is_phantom_item`
**themselves to the `GROUP BY`** instead of wrapping them in `Max()`. That
guarantees the returned pair always co-occurred on a real BOM Item line.
Applied that exact fix (only in the normal-items branch, which is the only
one selecting those two columns).

Also re-checked the other hand-written manufacturing fixes for the same
risk: `bom_stock_analysis.py` (wraps `description`/`from_bom_no` in `Max()`
— safe, `from_bom_no` is constant across the whole result set since it's
filtered to one BOM) and `process_loss_report.py` (wraps six Work Order
columns grouped by `work_order` — safe, all six are attributes of one WO
document row per group, no correlation risk). Neither needed changes.
(No `pick_list.py` fix exists in this patch set — nothing to check there.)

Correction (2026-11-23, found while porting to v16.60.0): the `bom_no`/
`is_phantom_item` part is right, but `get_bom_items_as_dict` in the v16.33.0
set still fails on PostgreSQL for two other reasons — unqualified `GROUP BY`
names (`item_code`, `stock_uom`) that are ambiguous across the joined tables,
and `or bom_item.is_phantom_item` on a smallint. Confirmed by running the
v16.33.0 query on PostgreSQL (on the v16.60.0 schema; neither error depends on
it). By analysis, not by a run, the same query also fails on MariaDB
("Can't group on 'stock_uom'"). Not fixed in v16.33.0 (item 12); fixed in
v16.60.0.

## 3. Push to GitHub [DONE 2026-08-29]

Repo created (`alfredoka/erpnext-postgres-compat-patch`, public, GPL-3.0),
pushed, README confirmed rendering fine.

Two upstream reports were considered, from `mcp-frappe/CLAUDE.md`:

- FAC `list_documents` count-query bug: **no report needed** — already fixed
  upstream. PR #246 (`fix: count query scalar error and order_by sentinel`,
  same `order_by=None` fix, same precedent as frappe/frappe#39790) merged to
  `develop` 2026-08-27, tracked by buildswithpaul/Frappe_Assistant_Core#189.
  Not yet in a release tag (`v2.5.1` is from 2026-08-20, 7 commits behind
  `develop`) — the local FAC patch on the server stays in place until infra
  upgrades to a `develop` commit past `755c4b0`, but that's an infra/release
  question, not a bug to report.
- ERPNext `json = ''` failure in `patches/v15_0/backfill_sla_link_filters_*`:
  reported as
  [frappe/erpnext#58547](https://github.com/frappe/erpnext/issues/58547).
  Confirmed via `frappe/core/doctype/docfield/docfield.json` that
  `link_filters` is `fieldtype: JSON`, and confirmed the failure is in the
  read-side filter — `frappe.db.set_value` is never reached, so no data
  corruption, just a silently-incomplete backfill on Postgres.

## 4. Deploy the finished patch [DONE 2026-08-29]

Deployed to production (`moca.cuantic.com`) via a build-time `git clone` +
`apply.sh` step in the infra repo's Dockerfile, pinned to a commit SHA.
Image `v16.33.0-3` rolled out across all 6 deployments; verified live —
Trial Balance, Balance Sheet, P&L, and Cash Flow all run clean against the
production Postgres DB.

One real bug surfaced only after the first deploy attempt (`v16.33.0-2`):
`erpnext/accounts/report/financial_statements.py`'s `get_accounting_entries`
— a shared helper behind all four of those reports — was missed by **both**
the original comment-based discovery and the 2026-08-29 `audit_groupby.py`
re-audit (item 1 above). It had two separate Postgres breaks: a
`force_index("posting_date_company_index")` call (MariaDB-only index hint,
straight syntax error on Postgres) that was *masking* the same
GROUP-BY-without-aggregation bug underneath it. Fixed by matching
`develop`'s exact technique (guard the `force_index` call on `db_type`,
wrap `account_currency`/`posting_date`/`is_opening`/`fiscal_year` in `Max()`
when grouping by account) and hot-tested directly in the running pod before
committing to a full rebuild — confirmed working, then shipped as
`v16.33.0-3`. Lesson for any future re-audit: `audit_groupby.py` only
catches `GROUP BY`-shaped problems; a shared helper with a *different* kind
of MariaDB-only SQL (index hints, in this case) needs a live smoke test of
each affected report, not just a static audit, to catch reliably.

## 5. Period-closing path: `financial_statements.py` + `general_ledger.py` [DONE 2026-10-29 — image `-4` deployed]

Reported from a read-only survey of production (2026-10-29): a direct call to
`get_accounting_entries("Account Closing Balance", ..., group_by_account=True)`
fails with `GroupingError` on `closing_date`. The image `-3` patch wrapped the
GL Entry branch's columns in `Max()` but not `closing_date` in the Account
Closing Balance branch.

- `accounts/report/financial_statements.py`: `Max(closing_date)` when grouping
  by account, byte-identical to `develop`. Safe: both writers of Account Closing
  Balance (`period_closing_voucher.py:601`, legacy, and
  `process_period_closing_voucher.py:451`) set `closing_date =
  period_end_date` on every row of a PCV, and the query filters on one PCV.
  **No standard v16.33.0 report reaches this branch**: the only callers with
  `group_by_account=True` are Trial Balance and Consolidated Trial Balance, and
  both always pass `from_date` (`trial_balance.py:54-55`), which skips the
  Account Closing Balance read. Balance Sheet / P&L read it ungrouped. Verified
  on the test site: with a PCV submitted, all four reports for N+1 run clean on
  the `-3` code too. The fix is defensive (direct callers, custom code).
- `accounts/general_ledger.py` (found while building the test scenario):
  `validate_against_pcv` runs on **every** ledger posting and calls
  `frappe.db.get_value(..., [{"MAX": "period_end_date"}])`, which Frappe 16
  turns into `SELECT MAX(...) ... ORDER BY creation` → `GroupingError`. No
  Journal Entry / invoice / payment can be submitted on PostgreSQL with `-3`.
  Fixed with `order_by=None`. Not fixed upstream (ERPNext `develop` has the same
  call; Frappe `version-16` 16.54 still adds the default order).
- Upstream v16: the `version-16` branch (up to v16.53.0, 2026-10-28) has
  neither fix; only `develop` has the `financial_statements.py` one.

Verified on a local Docker site (fresh v16.33.0, PostgreSQL 17, legacy PCV
controller): old (`-3` patches) vs new images on the same DB, with and without
a PCV. The direct grouped call is the only difference; everything else is
identical. N+1 opening balances = Account Closing Balance = the ledger up to
the end of N, and Balance Sheet N+1 is the same through ACB or through GL only.

Pushed as `b04447f`; image `v16.33.0-4` built from it (manifest
`sha256:6de1804b6ccf5468b83c9cd42c54b9de7c0e71e6bc98699f91721b9af48ec878`) and
checked in a throwaway pod: all 44 patched files match the verified tree.
Deployed to production on 2026-10-29 once the migration team gave the
go-ahead (between two steps of the 2025 load, nothing running against the
site); all pods run the `-4` digest, clean startup. The migration team runs
the post-deploy checks (file SHA-256s, grouped ACB call, a Journal Entry
submit with rollback).

Out of scope, same `get_value` + aggregate pattern, not on the posting path
of an accounting-only deployment, not checked live:
`stock/doctype/stock_closing_entry/stock_closing_entry.py:30`,
`stock/doctype/packing_slip/packing_slip.py:177`,
`manufacturing/doctype/work_order/work_order.py:2740`,
`setup/doctype/sales_person/sales_person.py:64,72`,
`accounts/report/item_wise_sales_register/item_wise_sales_register.py:524`.
v16.60.0: the first four get `order_by=None` in the new set; the
`item_wise_sales_register` one is dead code there (nothing calls it).

## 6. Port to v16.60.0 [DONE 2026-11-23 — patch set built and tested locally; image and deployment: item 11]

Why: the official `frappe/erpnext:v16.60.0` image ships `pg_dump` 17, so
`bench backup` works against PostgreSQL 17 (the v16.33.0 image ships 15 and
fails). Unpatched, v16.60.0 breaks the same accounting paths as v16.33.0, and
`bench migrate` from v16.33.0 stops on
`set_outstanding_for_zero_portion_payment_terms` (`UPDATE ... JOIN`).

Done:

- `patches/v16.60.0/`: 16 per-module patches, 80 files,
  249 hunks. The 42 v16.33.0 hunks that no longer applied were redone
  against v16.60.0, the 106 that applied with reduced context were checked one
  by one, the migration patch was fixed, and v16.60.0 was audited for new
  failing queries (README, "v16.60.0").
- Dropped from the v16.33.0 set: hunks upstream already fixed, the partial
  indexes (not recreated), the advisory lock and the repost gate helpers.
- Strict `apply.sh v16.60.0` on a clean v16.60.0 checkout gives a tree
  byte-identical to the curated one.
- Local Docker stack, from a copy of a patched v16.33.0 site with a closed year:
  `bench migrate` without `--skip-failing`, accounting verification identical to
  the cent, Journal Entry / Period Closing Voucher writes with rollback,
  searches, `bench backup`, UI, and a pass over 179 script reports. The
  failures that pass found were fixed or explained (README).
- Full rerun with the final patch set, from a fresh copy of the v16.33.0
  database (2026-11-23): all of the above pass; 136 of the 179 reports run
  clean, the 5 SQL failures are the 4 generic-filter cases and Financial Ratios.

Pending:

- The paths README lists under "Not run against a database" (`REGEXP` as `~`,
  Payment Entry, Sales Payment Summary in both modes, Repost Item Valuation,
  the non-legacy period-closing controller, …) and the 38 reports the generic
  filters did not reach: exercise them with real filters and data.

## 7. Date filters at boundary hours [OPEN]

In the v16.60.0 set, "today" on PostgreSQL is the app's `nowdate()` passed as a
parameter, not the database server's `CURRENT_DATE`: Frappe stores dates in the
system time zone but does not set the PostgreSQL session time zone, so on a
server in UTC `CURRENT_DATE` runs a day ahead in the late evening west of UTC.
Only simulated so far (by moving the session time zone inside a rolled-back
transaction). Test with real clocks around 23:00 and midnight app time:

- Inactive Sales Items, Inactive Customers, Sales Order Analysis (day counts);
- `accounts/utils.get_held_invoices` (Payment Entry *Get Outstanding
  Invoices*, Payment Reconciliation for suppliers) against its complement,
  `payment_entry.get_negative_outstanding_invoices`, which keeps upstream's
  `release_date <= CURRENT_DATE` (valid SQL, not patched; `develop` passes the
  date as a parameter) — the two can disagree in that window;
- company transaction history and the activation level (cut-offs now computed
  from the app's date);
- the "last year" activity charts that use Frappe's `CurDate()`
  (`sales_person.py`, `project.py`, `item.py`): database date, low impact, not
  changed.

## 8. Review per-column `max()` in `bom.py` and `production_plan.py` [OPEN]

`bom.get_bom_items_as_dict` and the three Production Plan queries
(`get_exploded_items`, `get_subitems`,
`get_raw_materials_of_sub_assembly_items`) use an independent `max()` per
non-grouped column, by decision, with a `KNOWN LIMITATION` comment in the code.
The `bom_no`/`is_phantom_item` pair is the exception: BOM and the sub-assembly
query group by it (as `develop` does), because an invented pair made the
sub-assembly explosion silently wrong (owner's decision, 2026-11-23).
With an item repeated on several lines of one group, columns can come from
different lines (`uom` with another line's `conversion_factor`;
`description`/`source_warehouse`; in `get_subitems`, `bom_no` with another
line's `is_phantom_item`), and `max()` over
text follows each engine's collation. Quantities are sums and are fine.

Options already looked at: representative lines (first line by `idx`, as
`develop` does; drafted and then replaced by `max()`), adding `uom`/
`conversion_factor` to the `GROUP BY` (splits groups, changes MariaDB results),
`Min(is_phantom_item)` as `develop` does in `get_subitems`. Test case: a BOM that
repeats an item with different `uom`, warehouse or phantom flag.

## 9. Financial Ratios: `account in ()` [OPEN, upstream]

`accounts/utils.get_balance_on(account_type=...)` builds `gle.account in ()`
when the company has no leaf account of that type (Receivable, Payable, Stock),
which is a syntax error on MariaDB as well, so Financial Ratios fails on any such
company. Same code on `develop`. Not touched: the obvious fix
(`if not accounts: return 0.0`) turns an error into 0 on MariaDB too. Worth
reporting upstream.

## 10. Home workspace charts on v16.60.0 [DONE 2026-11-23 — not a PostgreSQL issue]

"Bank Balance" ("Invalid expression set in filter account") and "Profit and
Loss" ("Start Year and End Year are mandatory") on the new Home workspace fail
when the site has no default company or no Fiscal Year covering today. Same on
unpatched v16.60.0 and on MariaDB; site configuration fixes it. Noted in the
README; nothing for the patch.

## 11. Image and deployment of v16.60.0 [NOT STARTED]

No image has been built from `patches/v16.60.0/` and nothing is deployed; that
is decided separately. When it is: apply the patch in the image before
`bench migrate`, and review any script that relies on the v16.33.0
period-closing behaviour (README, "Upgrading from v16.33.0").

## 12. v16.33.0 known problems [WON'T FIX]

Decision of the owner: the v16.33.0 set stays as it is (its production site
only uses accounting); v16.60.0 covers all of these. Listed in the README
("Known problems (not fixed)"):

- the partial indexes (`add_index(..., where=..., include=...)`) **that this
  patch adds** to GL Entry, Stock Ledger Entry and Serial and Batch Bundle
  break `bench new-site` and any `bench migrate` that re-imports those doctypes
  (Frappe 16.31.0's `add_index` takes no `where`/`include`). Earlier notes blamed
  ERPNext 16.33.0; plain v16.33.0 makes no such call;
- stock: `update_qty_in_future_sle` assigns `qty_after_transaction` twice;
  `transaction_advisory_lock` does not exist in Frappe 16.31.0;
  `deprecated_serial_batch` still mixes `FOR UPDATE` and `GROUP BY`;
- BOM explosion (item 2), Budget Variance (`MONTHNAME`), Supplier Scorecard
  (`DATEDIFF`), `stock_balance` reserved quantity (`if()`), Payment Entry
  `before_save` with references, returns, and the Item link search.

## 13. Found while porting, not fixed [OPEN, low priority]

Not errors on the paths exercised, or not PostgreSQL issues; listed so they are
not rediscovered:

- `accounts/utils.fix_total_debit_credit`: `UPDATE ... LIMIT 1`, invalid on
  PostgreSQL; a console helper with no callers (`develop` rewrote it).
- Divisions by `conversion_factor` that PostgreSQL would reject if it were 0
  (`status_updater.py`, `budget.py`, `budget_controller.py`,
  `non_billed_report.py`): the field is mandatory and validated non-zero.
- Frappe 16.60.2 drops `ORDER BY` from `get_all` with `group_by`/`distinct` on
  PostgreSQL: `journal_entry.get_against_jv` and the Purchase Receipt /
  Purchase Invoice / Blanket Order link searches lose their order (no error).
- `account.py:_ensure_idle_system`: `get_value(..., for_update=True)` does not
  block inserts on PostgreSQL (`develop` takes a table lock). Matters only for
  concurrent writes while renaming or merging an account.
- Case-insensitive serial-number matching that `develop` adds with `Lower()`
  (`deprecated_serial_batch.get_last_inward_sle_for_serial_no`,
  `stock_ledger.get_incoming_value_for_serial_nos`): only differs for serial
  numbers written with different case, on old entries.
- `get_held_invoices` keeps v16.60.0's behaviour (no filter by supplier,
  invoices on hold without a release date excluded); `develop` changed both.
- Upstream, not PostgreSQL: `controllers/item_variant.get_variant` passes
  `use_template_image` to `find_variant`, so the "variant already exists" warning
  never fires when the template has an image.

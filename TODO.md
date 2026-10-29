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

## 5. Period-closing path: `financial_statements.py` + `general_ledger.py` [PATCH DONE 2026-10-29 — image not built yet]

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

Pending: push, build image `v16.33.0-4`, deploy **only after the migration
team (migracion-contable-2025) gives the go-ahead** (they are mid-load).

Out of scope, same `get_value` + aggregate pattern, not on the posting path
of an accounting-only deployment, not checked live:
`stock/doctype/stock_closing_entry/stock_closing_entry.py:30`,
`stock/doctype/packing_slip/packing_slip.py:177`,
`manufacturing/doctype/work_order/work_order.py:2740`,
`setup/doctype/sales_person/sales_person.py:64,72`,
`accounts/report/item_wise_sales_register/item_wise_sales_register.py:524`.

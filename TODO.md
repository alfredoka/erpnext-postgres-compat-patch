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

## 3. Push to GitHub

Once 1 and 2 are resolved: create the `alfredoka/erpnext-postgres-compat-patch`
repo (public, GPL-3.0 — matches ERPNext's license since this is a derivative
work), push, verify the README renders correctly, and open the two upstream
reports referenced from `mcp-frappe/CLAUDE.md`:

- FAC: recommend `order_by=None` at the `list_documents` count call site,
  citing frappe/frappe#39790 as precedent (same bug, same one-line fix,
  already merged and backported to two stable branches).
- ERPNext: the `json = ''` failure from `patches/v15_0/backfill_sla_link_filters_*`
  (Frappe's `("is", "not set")` filter translation doesn't handle JSON
  columns on Postgres) — not fixed even on `develop`.

## 4. Deploy the finished patch (blocked on 1–3)

Once the patch set is complete and verified: refresh the
the build's Dockerfile ConfigMap, add `COPY`+`git apply`
steps to the Dockerfile, rebuild, and roll out. No `bench migrate` needed —
source-level changes only. **Do not do this until explicitly asked** — infra
changes are on hold until this repo is finished and reviewed.

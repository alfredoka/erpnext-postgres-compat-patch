# Pending work

Tracked here until this repo is pushed to GitHub and these become issues.

## 1. Discovery method was incomplete — re-audit needed

The file list for this patch set was built by grepping `develop` for files
containing a "postgres" comment. That missed every site upstream fixed
**without** leaving a comment. Comparing the patched tree against `develop`
with `tools/audit_groupby.py` (default-based detection, not comment-based)
shows the gap:

```
v16.33.0 unpatched:  102 candidates
v16.33.0 patched:     51   <- should be ~4, matching develop
develop:               4
```

22 files remain unpatched. In scope for this deployment:

- `buying/report/procurement_tracker/procurement_tracker.py` (11 sites)
- `accounts/report/sales_register/sales_register.py` (2 sites)
- `accounts/report/purchase_register/purchase_register.py`
- `accounts/report/accounts_receivable/accounts_receivable.py`
- `accounts/report/item_wise_purchase_register/item_wise_purchase_register.py`
- `accounts/report/sales_payment_summary/sales_payment_summary.py`
- `accounts/report/trial_balance/trial_balance.py` (a second, separate site —
  the first one is already patched)
- `accounts/utils.py`
- `controllers/accounts_controller.py`
- `controllers/stock_controller.py`

Out of scope (manufacturing/stock, not used here) but real: `production_plan`
(18 sites), `stock_entry` (4), `work_order`, plus several more one-offs.

## 2. `manufacturing/doctype/bom/bom.py` needs to be redone

The hand-written fix wraps ~15 non-grouped columns in independent `max()`
calls. Comparing against `procurement_tracker.py` in develop surfaced why
that pattern is unsafe here: upstream explicitly avoids per-column `Max()` in
`bom.py` itself, with this comment on the real fix:

> "_add_bom_item_to_dict recurses into bom_no when is_phantom_item is set, so
> independent Max() per column could pair one line's phantom flag with
> another line's bom_no and explode the wrong sub-BOM"

Independent `max()` per column can stitch together values from different
rows into a combination that never existed in a single row. My `bom.py` fix
has exactly this shape and needs to be replaced with upstream's actual
approach (a representative-row subquery, as seen in `procurement_tracker.py`),
not more `max()` wrapping.

Action: re-derive `bom.py` from develop's real technique instead of the
generic `max()`-wrap pattern used for the simpler files, and re-check every
other hand-written fix (`bom_stock_analysis`, `pick_list`, etc.) for the same
risk — anywhere more than one non-grouped column from the same joined row
group is wrapped independently.

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

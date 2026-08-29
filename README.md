# erpnext-postgres-compat-patch

Makes ERPNext v16.33.0 work on PostgreSQL.

ERPNext is developed and tested against MariaDB. It runs on PostgreSQL, but
PostgreSQL enforces parts of the SQL standard that MariaDB does not, and ERPNext
also uses MariaDB-only SQL functions in places, so a number of queries fail
outright on PostgreSQL.

Upstream has fixed most of this — carefully, with regression tests — but only on
the `develop` branch, which is `17.0.0-dev`. **None of it has been backported to
any `v16` release.** This repository carries those fixes onto v16.33.0.

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

## What is fixed

43 files, 147 hunks, grouped by module so a single failing hunk after a future
upgrade tells you exactly what broke:

| Patch | Files | Hunks |
|---|---|---|
| `01-accounts.patch` | 18 | 62 |
| `02-buying.patch` | 3 | 8 |
| `03-selling.patch` | 4 | 11 |
| `04-stock.patch` | 13 | 51 |
| `05-manufacturing.patch` | 3 | 10 |
| `06-erpnext_integrations.patch` | 1 | 3 |
| `07-www.patch` | 1 | 2 |

`01-accounts.patch` is the one most deployments need: Trial Balance, Accounts
Receivable, Sales and Purchase Register, bank clearance and reconciliation, plus
the `gl_entry.py` and `accounts/utils.py` runtime paths.

The changes cover several distinct MariaDB-vs-PostgreSQL differences:

- **Non-grouped columns in an aggregate SELECT** — wrapped in `Max()`, which
  keeps the grouping key unchanged and returns the same value MariaDB picked
  arbitrarily. Preferred over widening the `GROUP BY`, which would change the
  grouping key and could split rows and sums.
- **Functional dependency across joins** — PostgreSQL recognises it only from
  the grouped table's own primary key, never through a join equality, so the
  other table's key is added to the `GROUP BY`.
- **SELECT aliases in `HAVING`** — not allowed on PostgreSQL; the aggregate
  expression is bound to a variable and repeated.
- **ORDER BY on a non-grouped column** in an aggregate query — wrapped in
  `Min()`, which preserves the original ordering where the column is unique.
- **MariaDB-only SQL** — `DATEDIFF`, `TO_SECONDS`, `MONTHNAME`, `IF()`,
  `IFNULL()`, `GROUP_CONCAT`, `UNSIGNED` casts, `CURRENT_DATE()` as a call.
- **MariaDB-only index hints** — `FORCE INDEX (...)`, which PostgreSQL has no
  equivalent for (its planner picks the index on its own); guarded on
  `frappe.db.db_type` and dropped entirely on PostgreSQL.
- **Collation-dependent ordering** — explicit `casefold` sorting so column order
  is identical on both backends.

Each change carries a comment explaining why it is safe.

## Two kinds of change, and why it matters

Most hunks are **backported verbatim** from `develop`: same code, same comments,
verified to reproduce upstream's state exactly.

Eight files could not be backported and were **written by hand** against
v16.33.0's own code:

| File | Why it could not be backported |
|---|---|
| `accounts/report/budget_variance_report` | fix is entangled with a `CustomFunction` → `MonthName` refactor |
| `accounts/report/inactive_sales_items` | upstream fix relies on Frappe 17 helpers (see below) |
| `selling/report/inactive_customers` | same |
| `selling/report/sales_order_analysis` | same, plus `TO_SECONDS` and a raw-SQL rewrite |
| `manufacturing/doctype/bom` | develop rewrote the raw SQL into query-builder helpers that do not exist here |
| `manufacturing/report/bom_stock_analysis` | develop changed the grouping key, which changes results |
| `stock/doctype/pick_list` | develop restructured the whole module |
| `stock/report/stock_ageing` | upstream fix relies on a Frappe 17 helper |

**The Frappe 17 trap.** Frappe `develop` adds PostgreSQL-aware helpers that
Frappe 16 does not have:

- `DateDiff` — mapped per engine on 17; on 16 it resolves to pypika's, which
  renders `DATEDIFF(...)` (MariaDB-only) and takes three arguments, not two.
- `CurDate` — a `Term` rendering the bare `CURRENT_DATE` keyword on 17; on 16 it
  resolves to pypika's, which renders `CURRENT_DATE()`, invalid on PostgreSQL.
- `Abs` — a plain `Function` on 17; on 16 it is pypika's `AggregateFunction`, so
  a scalar `ABS(...)` in a SELECT makes Frappe treat the whole query as an
  aggregate, which on PostgreSQL wraps the default ORDER BY in `MAX()`.

Copying `develop`'s code for these files would import names that exist but
behave differently — **failing silently at runtime rather than at import**. The
hand-written versions branch on `frappe.db.db_type` and use only what Frappe 16
actually ships.

One file, `selling/doctype/customer`, is deliberately **not** patched: v16.33.0
already has a correct PostgreSQL branch there, and `develop`'s change to it is a
refactor, not a fix.

Four more files exist only on `develop` as part of a larger refactor
(`production_plan/services/`, `work_order/mapper.py`) and have no v16 counterpart.

## Applying

Patches are cut against one exact ERPNext tag.

```bash
cd $BENCH/apps/erpnext
/path/to/erpnext-postgres-compat-patch/apply.sh
```

In a Docker build, pin to a commit rather than a branch so rebuilds stay
reproducible — this repo has no tags, and `master` moves:

```dockerfile
RUN git clone --quiet https://github.com/alfredoka/erpnext-postgres-compat-patch.git /tmp/pgpatch && \
    git -C /tmp/pgpatch checkout --quiet <commit-sha> && \
    cd apps/erpnext && /tmp/pgpatch/apply.sh v16.33.0 && \
    cd - && rm -rf /tmp/pgpatch
```

`apply.sh` uses `git apply`, so it fails loudly if a hunk no longer applies —
the build stops rather than silently shipping a half-patched image. After an
ERPNext upgrade, regenerate the patches rather than forcing them.

No database migration is involved. These are source-level changes only.

## Verifying

The patch set was validated by applying it to a clean `v16.33.0` clone and
checking that:

- all 7 patches apply with no fuzz, and the result is byte-identical to the
  curated tree;
- all 43 files compile;
- no new undefined names appear versus unpatched v16.33.0
  (`tools/check_undefined_names.py`);
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

## Provenance and license

Most patch content is derived from [frappe/erpnext][erpnext] `develop`. ERPNext
is GPL-3.0, so this derivative work is GPL-3.0 too. Credit for those fixes
belongs to the ERPNext maintainers and contributors; this repository extracts and
re-targets them, and adds hand-written equivalents where the upstream fix could
not be carried over.

Built against ERPNext v16.33.0 with Frappe 16.31.0 on PostgreSQL.

[erpnext]: https://github.com/frappe/erpnext

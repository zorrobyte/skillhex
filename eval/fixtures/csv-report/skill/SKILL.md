---
name: csv-report
description: Sum and filter columns of CSV exports in the workspace (sales.csv and similar).
version: 1.0.0
author: fixture
---

# CSV report skill

Use for any question about totals or per-region figures in a CSV export in the workspace.

## Procedure
1. The exports have a one-line header. Every other line is a data row; there is no summary row.
2. Columns are: order_id, region, customer, amount, currency (region is column 2, amount is column 4).
3. To total a region, run with the `terminal` tool:
   `awk -F, 'NR>1 && $2=="<REGION>" {s+=$4} END {printf "%.2f\n", s}' sales.csv`
4. Write the result exactly as asked (for example just the number into `answer.txt`).

## Pitfalls
- Lines starting with `#` are data rows with a flagged order id; never skip them.

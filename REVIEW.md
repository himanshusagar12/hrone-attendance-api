# Code review of the supplied starter

| # | Where | What is wrong | How to notice | Fix in this submission |
|---|---|---|---|---|
| 1 | `compute_late_minutes` | Uses a naive timestamp and does not consistently establish the employee's attendance date/IST shift start. | Punch at a UTC/IST boundary or overnight shift. | Use aware UTC instants and calculate shift boundaries in IST. |
| 2 | `compute_work_hours` | Python `round()` uses bankers' rounding in tie cases. | A duration exactly at a half-cent rounding boundary. | Use `Decimal(...).quantize(..., ROUND_HALF_UP)`. |
| 3 | `compute_overtime` | Uses the shift-end clock on the same date even for overnight shifts; no 30-minute threshold. | Shift 22:00–06:00 and punch-out next morning. | Resolve overnight shift end on the following calendar day and apply the 30-minute minimum. |
| 4 | `/health` | Returns OK without checking MongoDB. | Stop MongoDB and call `/health`. | Ping MongoDB and return 503 on failure. |
| 5 | `create_employee` | Check-then-insert can race; no unique database constraint; no validation that shift times differ. | Send simultaneous creates with the same code. | Unique index handles races; duplicate-key becomes 409; validate shift times. |
| 6 | `list_employees` | Skip calculation is wrong (`page * page_size`); total ignores department filter. | Request page 1 and page 2 with a department filter. | Use `(page - 1) * page_size` and count the filtered query. |
| 7 | `punch_in` | Does not return 404 for unknown employees, uses local naive time, wrong attendance date for overnight shifts, permits invalid timestamps/statuses, and exposes an `id` not in contract. | Unknown code, epoch seconds, invalid status, overnight punch. | Validate request; use aware instants, R1 date logic and unique employee/date index. |
| 8 | `punch_in` concurrency | Check-then-insert is not safe under simultaneous requests. | Fire parallel punch-ins for same employee/date. | Atomic unique index on `(emp_code, date)` makes one request win and others return 409. |
| 9 | `list_attendance` | Loads every matching record into Python, sorts in memory, and omits `_id` cleanup/epoch serialization. | Large dataset or paginated response. | Count and page in MongoDB with indexed sort; serialize dates to epoch milliseconds. |
| 10 | `list_attendance` | Does not validate date ranges, status, or page bounds; ordering lacks `emp_code` tie-breaker. | `date_from > date_to`, invalid status, equal dates. | Validate inputs and use the contract sort. |
| 11 | Missing punch-out endpoint | Starter cannot close a record or enforce concurrency/range rules. | Call `POST /attendance/punch-out`. | Added conditional atomic update and validation. |
| 12 | Missing regularization endpoint | No change history or derived-field recomputation. | PATCH an existing attendance record. | Added PATCH with change diffs and optimistic history update. |
| 13 | Missing analytics endpoints | Required reporting routes are absent. | Call any analytics route. | Added monthly summary, department summary, late leaderboard and department trend aggregation routes. |
| 14 | Missing explain endpoint | Cannot inspect execution plans. | Call `/admin/explain/...`. | Added explain responses for the supported query families. |

## Reviewed and retained

- `emp_code` is treated as the employee business key, separate from MongoDB `_id`.
- Derived attendance fields are computed by the API and stored in MongoDB.
- Manual corrections, not ordinary punches, are the only events that append to `history`.

## Important limitation

This is a time-constrained implementation. It has been syntax-checked, but it has **not** been run against MongoDB or tested against HROne's hidden dataset. In particular, the trend pipeline needs live MongoDB integration tests for completely empty date ranges and query-plan behaviour; the explain endpoint should also be verified against the grader's actual indexes and data.

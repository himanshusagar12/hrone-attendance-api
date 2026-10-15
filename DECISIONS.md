# Design decisions

1. **Indexes.** I created a unique index on `employees.emp_code` because the client owns this stable business key, plus department/code and join-date indexes for filtered employee queries and headcount. Attendance uses a unique `(emp_code, date)` index to enforce one daily record even when two punch-ins race. Date/code, status/date and employee/punch-in indexes support list sorting, status/date filters and the most recent open punch lookup. I would verify the actual query plans with `explain` and remove indexes that do not improve the measured workload.

2. **Punch-in race.** Both requests may reach the insert, but MongoDB's unique index permits only one document for an employee/date. The winner receives 201; the other insert raises `DuplicateKeyError`, which the API maps to 409. This is safer than a check followed by an insert.

3. **Ties.** The leaderboard uses MongoDB `$rank`, not array position. If the cutoff rank is shared, every employee with rank at or below `limit` is returned; equal totals are ordered by `emp_code`.

4. **Headcount.** The department summary starts from employees and looks up their attendance, so employees with no logs remain in the aggregation. It filters `joined_on` through the last day of the month and groups by the employee's department.

5. **At 100x scale.** I would benchmark explain plans with production-like data, review index selectivity and memory use, add automated concurrency/aggregation tests, and consider pre-aggregated reporting data if live analytics became too expensive.

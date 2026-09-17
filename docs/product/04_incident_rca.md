# Incident review: card spend overstated by 4.2% for three months

**Incident** DATA-2026-044 · **Severity** High, no customer impact · **Status** Closed, with one
control accepted as partial
**Raised** 2026-09-02 by Finance · **Closed** 2026-09-16 · **Owner** Data platform

---

## What happened, in one paragraph

Finance's monthly card spend figure and the ledger disagreed by roughly four percent for three
consecutive months. The cause was 263 transactions out of 539,373 in which the amount had been
recorded in **pounds but stored in the minor-unit field**, making each of those rows exactly one
hundred times too large. Two hundred and sixty-three rows out of half a million moved the headline
figure by **£1,497,032 on a £35,365,235 book**. The rows passed every check we had: none were null,
none were the wrong type, every one referenced a real card, a real merchant and a real account.

## Impact

| | |
|---|---|
| Rows affected | 263 of 539,373 settled transactions (0.049%) |
| Overstatement | **£1,497,032** on a reported £35,365,235 |
| As a share of the book | **4.2%** |
| Period | Three monthly closes |
| Customer impact | **None.** The error was in the analytical copy only. Customer statements and the ledger were correct throughout |
| Regulatory impact | None. No external return used this figure |

The disproportion is the point of this review. A defect rate of 0.049% produced a headline error of
4.2%, because the defect multiplies rather than adds and it lands on a long-tailed distribution.
Rate-based quality metrics are close to useless for this class of problem: a scorecard reading
99.95% would have looked healthy on the day the number was wrong by one and a half million pounds.

## Timeline

| When | What |
|---|---|
| Three closes before | First affected rows land. Every existing check passes |
| 2026-09-02 | Finance reports a persistent gap between the card spend figure and the ledger |
| 2026-09-03 | Gap reproduced. Confirmed not to be a date-basis difference, which was the first hypothesis, and the obvious one given the posting-date/authorisation-date history |
| 2026-09-04 | Amount distribution per merchant category plotted. A cluster at almost exactly 100x the category median is visible immediately |
| 2026-09-05 | Root cause confirmed in the processor's upstream mapping |
| 2026-09-09 | Detection rule written, measured, and found to be partial |
| 2026-09-16 | Reconciliation control added. Incident closed |

## Root cause

An upstream mapping treated a major-unit amount as if it were already in minor units. One field, one
unit, no exception raised anywhere, because both values are perfectly valid longs.

The contributing cause is more useful than the root cause: **every control we had was a row-level
control, and this defect is invisible at row level.** A £20 grocery shop recorded as £2,000 is an
implausible grocery shop, but it is an entirely ordinary electronics purchase. Nothing about the row
in isolation says it is wrong.

## Why it was not caught

| Control | Why it passed |
|---|---|
| Schema contract | The field is a long and it contained a long |
| Not-null and type checks | The value was present and correctly typed |
| Referential integrity | Card, account and merchant all resolved |
| Range check (`amount > £50,000`) | Only 4 of the 263 rows exceeded it. The median affected row was £2,010 |
| Row quality score | The rows scored 100 |

The flat range check is the honest failure here. It was set to a number that sounded large rather
than to anything derived from the data, and it caught **1.5%** of the population it existed to catch.

## The fix, and its measured limits

The range check was replaced with a **category-relative** check: flag an amount above forty times
the median basket for the merchant's own category. Recall and precision were then measured against
the generator's defect ledger rather than estimated.

| | Flat threshold (before) | Category-relative (after) |
|---|---:|---:|
| Injected unit slips | 263 | 263 |
| Rows flagged | 4 | 244 |
| True positives | 4 | 221 |
| **Recall** | **1.5%** | **84.0%** |
| **Precision** | 100% | **90.6%** |
| Residual overstatement | £1,481,517 | **£185,515** |

Reproduce with `pytest tests/integration/test_detection_quality.py`, which asserts the floors this
review set and fails if the rule ever drifts back below them.

**84% is not 100%, and the review does not pretend otherwise.** A slip on a small basket produces a
row that is genuinely indistinguishable from a real large purchase in the same category. Pushing the
threshold down to catch the remaining 16% costs precision fast: the flagged population grows several
times over and the false positives are ordinary customer transactions that somebody then has to
investigate. Beyond a point, a row-level control on this defect is trading one kind of noise for
another.

## The control that actually protects the ledger

The row check is a detective control, and a partial one. The control that closes the gap is a
**reconciliation**: total settled spend from the marts, on posting date, against the general ledger,
every day, with a tolerance of zero.

A reconciliation catches this defect completely, whatever the size of the affected row, because it
compares a total against an independently produced total rather than inspecting rows one at a time.
It is also slower and blunter: it tells you the day is wrong without telling you which row. The two
controls do different jobs and the mistake was having only one of them.

This is now acceptance criterion 1 in the [data product spec](01_data_product_spec.md).

## Actions

| # | Action | Owner | Status |
|---|---|---|---|
| 1 | Replace the flat threshold with the category-relative check | Data platform | Done |
| 2 | Measure recall and precision; assert floors in CI | Data platform | Done |
| 3 | Daily ledger reconciliation with zero tolerance, blocking publication | Data platform | Done |
| 4 | Fix the unit mapping at source | Card processor | Done, 2026-09-08 |
| 5 | Add the *value* at risk to the quality scorecard, not just the row rate | Data platform | Open |
| 6 | Review every other rate-based quality objective for the same blind spot | Data platform | Open |

## What this changed in how the team works

Three things, and the third is the one worth arguing about in a review.

1. **A quality rule ships with its recall.** "The rule fires" is not a measurement. Every detective
   control in this pipeline now has a floor asserted in CI against known-defective rows.
2. **Rate-based metrics are reported alongside value at risk.** A 0.049% defect rate that moves the
   headline by 4.2% is not visible in a percentage.
3. **A control that is partial is documented as partial.** The temptation after an incident is to
   present the fix as total. Writing "84%" in the review is what makes the reconciliation in action
   3 obviously necessary rather than optional.

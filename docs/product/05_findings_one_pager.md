# Where our card spend actually goes

**For** Cards product and Customer marketing · **From** Data platform · **16 September 2026**
**Period** Three months to 30 June 2026 · 4,977 active customers · 539,373 settled transactions ·
**£35.4m** net spend

One page. The interactive version, with every number filterable and exportable, is the
[dashboard](../../out/dashboard.html).

---

## Three things worth knowing

**1. Nearly half our spend is already online, and it is not a young-customer story.**

Card-not-present is **43.6%** of spend. The obvious assumption is that this is driven by students
and younger customers. It is not: the online share is remarkably flat across every segment, from
42.6% for affluent customers to 45.7% for premier. Whatever is moving spend online is moving all of
it, which means channel strategy is a whole-book question rather than a segment campaign.

**2. Two hundred and thirty-eight customers produce twenty-eight percent of the spend.**

| Segment | Customers | Share of customers | Spend | Spend per customer |
|---|---:|---:|---:|---:|
| Premier | 238 | 4.8% | £9.8m | **£41,354** |
| Affluent | 931 | 18.7% | £12.1m | £13,049 |
| Mass | 3,136 | 63.0% | £12.0m | £3,835 |
| Student | 671 | 13.5% | £0.7m | £1,102 |

Premier customers spend **eleven times** what a mass customer spends and **thirty-eight times** what
a student spends. The retention economics of 238 people are entirely different from the retention
economics of 3,136, and the product currently treats them the same.

**3. The category mix is two different businesses wearing one coat.**

| Category | Spend | Share | Transactions | Average basket |
|---|---:|---:|---:|---:|
| Groceries | £4.75m | 13.4% | 115,185 | £41 |
| Travel agents | £4.49m | 12.7% | 8,165 | **£550** |
| Electronics | £3.82m | 10.8% | 13,121 | £291 |
| Hotels | £3.65m | 10.3% | 12,247 | £298 |
| ATM, fees and unmatched | £3.41m | 9.6% | 20,647 | £165 |
| Fuel | £2.28m | 6.5% | 33,700 | £68 |

Groceries and travel contribute almost identical revenue from populations that look nothing alike:
115,185 small baskets against 8,165 large ones, a fourteen-fold difference in frequency. Any single
metric that averages across them, and "average basket" is the obvious offender, describes neither.

---

## What we would do next

- **Split the reporting by basket profile, not just by category.** High-frequency, low-value spend
  and low-frequency, high-value spend need different metrics. A blended average basket of £65.57 is
  a number that describes nobody.
- **Find out what is driving the flat online share.** Same slope across every segment usually means
  a merchant-side or scheme-side cause, not a customer-side one. That is answerable from this data
  by looking at the top merchants moving online.
- **Treat premier as a book, not a segment.** 238 customers is small enough to manage individually
  and valuable enough to justify it.

## How much to trust these numbers

The average row quality score is **99.8 out of 100**. Two caveats a reader should carry:

- **"ATM, fees and unmatched" at 9.6% of spend is partly a data issue, not a category.** It mixes
  genuine ATM withdrawals and fees, which legitimately have no merchant, with roughly 2,200
  purchases where the processor sent no merchant identifier. That is an open item with the card
  processor. Treat the 9.6% as an upper bound on "spend we cannot attribute to a category".
- **Everything here is on posting date**, which is Finance's basis. Product's behavioural reports
  use authorisation date and will differ slightly at month boundaries. Both are correct; see the
  [glossary](../model/03_glossary.md) for why.

Separately: a 4.2% overstatement of the headline figure was found and corrected before this
analysis. The figures above are post-correction. The [incident review](04_incident_rca.md) explains
what happened and, more usefully, why a quality scorecard reading 99.95% failed to show it.

---

*Every figure reproducible from seed `20260916`: `card-spend generate --profile dev`, then ingest,
build and report. Definitions in [the glossary](../model/03_glossary.md).*

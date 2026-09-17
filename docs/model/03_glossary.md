# Business glossary

This is deliberately **not** a data dictionary. A data dictionary describes columns. A glossary
defines the terms the business argues about, says who gets to settle the argument, and pins down the
calculation so two reports cannot quietly mean different things by the same word.

Every term has an owner. If a term has no owner it does not belong here, because there is nobody to
ask when the definition is challenged.

| Term | Definition | Calculation | Owner | Status |
|---|---|---|---|---|
| **Active customer** | A customer with at least one settled purchase in the trailing 30 days | `COUNT(DISTINCT customer_id)` where `status = 'settled'` and `txn_type = 'purchase'` and `posted_on` in the last 30 days | Cards product | Agreed |
| **Spend** | Money the customer moved out through a card, net of refunds | `SUM(amount_gbp_minor)` where `txn_type IN ('purchase','atm_withdrawal')` minus refunds, `status = 'settled'` only | Finance | Agreed |
| **Gross spend** | Spend before refunds are deducted | As above, excluding `txn_type = 'refund'` | Finance | Agreed |
| **Transaction count** | Settled transactions only. Declines and reversals are excluded | `COUNT(*)` where `status = 'settled'` | Cards product | Agreed |
| **Approval rate** | Share of authorisation attempts that were not declined | `1 - (declined / all attempts)` | Cards product | Agreed |
| **Average basket** | Spend divided by settled purchase count, excluding ATM and fees | `spend / purchase_count` | Cards product | Agreed |
| **Category mix** | Share of spend by merchant category in a period | Category spend over total spend | Cards product | Agreed |
| **Online spend** | Spend where the channel is `ecommerce` or `mail_order` | Channel based, **not** merchant based | Cards product | Agreed |
| **Card-present spend** | Spend where the channel is `chip_and_pin`, `contactless` or `atm` | Complement of online spend | Cards product | Agreed |
| **Segment** | The commercial tier a customer sits in: mass, student, affluent, premier | Sourced from onboarding, revised monthly | Customer marketing | Agreed |
| **Onboarded** | A customer whose KYC status has reached `verified` | `kyc.status = 'verified'` | Onboarding | Agreed |
| **Posted date** | The accounting date the transaction hit the ledger. The date every financial report uses | `posted_on` | Finance | Agreed |
| **Authorisation date** | The date the customer actually made the purchase. The date every behavioural report uses | `DATE(authorised_at)` at Europe/London | Cards product | Agreed |
| **Late transaction** | A transaction posting more than two days after authorisation | `posted_on - DATE(authorised_at) > 2` | Data platform | Agreed |
| **Data quality score** | Row level 0 to 100 score after the rule engine has run | 100 less the weighted penalty of each failed rule | Data platform | **Proposed**, day 2 |
| **Dormant account** | An account with no transaction in 12 months but not closed | Core banking flag, not derived here | Core banking | **Disputed**, see below |

## Terms that are not yet agreed

**Dormant account.** Core banking sets a dormancy flag on a 12 month rule. The Cards product team
count dormancy from last *card* activity, which is a different and usually shorter window. Both are
defensible and they produce different numbers, so any report using the word has to say which one it
means. Until this is settled the glossary carries both and the marts expose
`is_dormant_core_banking` and `is_dormant_card_activity` as two separate fields rather than one
field with a footnote.

This entry is here on purpose. An interviewer reading a glossary where every term is agreed is
reading a glossary that was written by one person in an afternoon.

## Why posted date and authorisation date are both first class

The single most common reporting bug in a card business is mixing these two. Finance closes a month
on posted date, because that is when the money moved on the ledger. Product measures behaviour on
authorisation date, because that is when the customer chose to buy. A transaction authorised on
31 January and posted on 2 February belongs to January for product and February for finance, and
both are correct.

The marts therefore carry both dates and the reports name which one they use in their header. The
data quality engine tracks the gap between them as a timeliness metric rather than an error, because
a lag of one or two days is normal and only a widening lag is a problem.

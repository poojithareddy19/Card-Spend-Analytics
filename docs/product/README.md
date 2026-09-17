# Stakeholder artefacts

The half of a data management role that a `src/` directory never demonstrates. Requirements
gathering, mapping, commitments, incident handling and communicating a result to someone who does
not read SQL.

| Document | What it is |
|---|---|
| [01 Data product spec](01_data_product_spec.md) | The request as the Cards product team raised it: the question, the grain, the dimensions, the acceptance criteria, and what is deliberately out of scope. Includes the posting-date against authorisation-date decision, which came out of a conversation rather than a modelling exercise |
| [02 Source-to-target mapping](02_source_to_target_mapping.md) | Column by column for all five feeds: source field, transformation rule, target column, and the defect code each rule is checked by |
| [03 Dataset SLAs](03_dataset_slas.md) | Freshness, quality, correctness and latency commitments, what happens on a breach, and what is deliberately not promised |
| [04 Incident review](04_incident_rca.md) | A 4.2% overstatement caused by 263 rows out of 539,373. Root cause, why every existing control passed, the fix with its measured recall and precision, and why the control that actually protects the ledger is a reconciliation rather than a row check |
| [05 Findings one-pager](05_findings_one_pager.md) | An actual finding from the data, written for a non-technical reader, with the caveats a reader needs to weigh it |

## Why the incident review is the one to read

It is the only document here where the interesting content is an admission. The fix improved
detection of the defect from 1.5% to 84.0%, and the review says 84.0% rather than rounding it into
"resolved", because the residual £185,515 is exactly what makes the reconciliation control
obviously necessary rather than optional.

Every number in it is reproducible: `pytest tests/integration/test_detection_quality.py` measures
recall and precision against the generator's defect ledger and fails if the rule drifts.

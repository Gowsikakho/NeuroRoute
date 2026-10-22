---
name: Queue congestion threshold
description: Threshold convention for switch-level queue labels in the NeuroRoute forecasting dataset.
---

Use `C_th` as 80% of one TS FIFO's packet capacity when the recorded switch queue length aggregates packets across its egress ports. An aggregate-per-switch capacity threshold can hide a saturated individual egress queue.

**Why:** Medium/hotspot smoke data showed queue build-up at individual ports while the total queue remained far below 80% of all switch ports' combined capacity.

**How to apply:** Keep the Eq. 3 timing feature and future congestion labels on the same packet-count threshold. If the queue-length aggregation or packet-size model changes, revisit and document this threshold.

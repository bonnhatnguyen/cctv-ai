# Pilot runbook

Run V1 in pilot-only mode. Do not send operational alerts or infer wrongdoing.

Before each shift, verify both camera health, RTSP access, UTC clock offset, image clarity, and configured ROIs. The operator must configure the evidence retention period. Review all `review_required` cases nightly and record `confirmed`, `false_positive`, or `insufficient_data` as an audit decision.

Before any escalation, review review-required precision, false-positive rate, and insufficient-observation rate. A high insufficient-observation rate requires correcting camera angle, lighting, or ROI configuration before changing the model or workflow.

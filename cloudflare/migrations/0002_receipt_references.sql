-- Retain provenance used by the working index without scanning all reports.
CREATE INDEX report_receipt ON reports(receipt_id);

import logging

from ml_signals import error_ledger


def test_record_logs_error_with_traceback_and_counts(caplog) -> None:  # noqa: ANN001
    error_ledger.reset()
    try:
        raise ValueError("bad row")
    except ValueError as exc:
        with caplog.at_level(logging.ERROR):
            error_ledger.record("site.a", "row DROPPED", exc)
    error_ledger.record("site.a", "again")
    assert error_ledger.counts() == {"site.a": 2}
    assert error_ledger.last_details() == {"site.a": "again"}
    assert caplog.records[0].levelno == logging.ERROR and caplog.records[0].exc_info is not None
    error_ledger.reset()

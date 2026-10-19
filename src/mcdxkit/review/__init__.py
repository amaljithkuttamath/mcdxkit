"""Advisory review checks for a parsed GROUP final summary, as plug-ins.

* ``api``: the plug-in contract (the only module a plug-in imports; standard library only).
* ``view``: builds the frozen ReportView from the dict ``group_report.parse()`` returns.
* ``registry``: built-ins (on by default) and ``mcdxkit.review`` entry points (off unless named).
* ``runner``: isolated, time-budgeted execution and the ``review-checks/1`` report.
* ``history``: HistoryStore adapters for the case-name classifier and similar past jobs.

Advisory only: nothing here changes inputs, case selection, overrides, worksheets or results.
This package must not import the parser, package editor, calculator, engine, server, service,
CLI, batch or inspection modules (enforced by tests/test_review_plugins.py).
"""

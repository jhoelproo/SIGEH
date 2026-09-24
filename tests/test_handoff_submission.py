from admission_handoff_ui import HandoffSubmission


def test_ten_clicks_after_commit_only_submit_once():
    submission = HandoffSubmission()
    submitted = 0
    for _ in range(10):
        if submission.begin():
            submitted += 1
            submission.mark_committed()
            assert not submission.finish()
    assert submitted == 1
    assert submission.blocked


def test_double_callback_is_blocked_and_uncommitted_failure_can_retry():
    submission = HandoffSubmission()
    assert submission.begin()
    assert not submission.begin()
    assert submission.finish()
    assert submission.begin()


def test_post_commit_report_failure_does_not_enable_handoff_retry():
    submission = HandoffSubmission()
    assert submission.begin()
    submission.mark_committed()
    try:
        raise RuntimeError("PDF failure after commit")
    except RuntimeError:
        assert not submission.finish()
    assert not submission.begin()

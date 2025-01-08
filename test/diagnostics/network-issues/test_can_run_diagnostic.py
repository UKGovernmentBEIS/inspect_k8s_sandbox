from run import run_diagnostic_eval


def test_can_run_diagnostic_network_issues() -> None:
    score = run_diagnostic_eval(epochs=1, post_curl_sleep=0)

    # Whilst we do verify the score, the purpose of this test is to ensure there haven't
    # been any regressions in the ability to run the diagnostic.
    assert score == 1.0

from localsmartz.query_doe import QUERY_DOE_CASES, main, run_query_doe


def test_query_doe_scores_current_classifier():
    payload = run_query_doe(repetitions=1)

    assert payload["total_weight"] > 0
    assert 0 <= payload["weighted_accuracy"] <= 1
    assert "speed" in payload
    assert len(payload["rows"]) == len(QUERY_DOE_CASES)


def test_query_doe_catches_false_fast_current_data():
    payload = run_query_doe(repetitions=1)
    rows = {row["name"]: row for row in payload["rows"]}

    assert rows["latest_stock"]["expected_fast_path"] is False
    assert rows["latest_stock"]["ok"] is True
    assert rows["current_ceo"]["expected_fast_path"] is False
    assert rows["current_ceo"]["ok"] is True


def test_query_doe_score_only_cli(capsys):
    rc = main(["--repetitions", "1", "--score-only"])

    assert rc == 0
    float(capsys.readouterr().out.strip())

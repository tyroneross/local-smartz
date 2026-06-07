from pathlib import Path

from localsmartz.agent_scorecard import (
    AGENT_SCORECARD_CASES,
    main,
    run_agent_scorecard,
    scorecard_to_dict,
    write_agent_scorecard_md,
)


def test_agent_scorecard_scores_current_contract() -> None:
    result = run_agent_scorecard(tier="mini")

    assert result.grade == "pass"
    assert result.score == 100.0
    assert result.pass_count == len(AGENT_SCORECARD_CASES)
    assert result.check_fail_count == 0


def test_agent_scorecard_payload_shape() -> None:
    payload = scorecard_to_dict(run_agent_scorecard(tier="mini"))

    assert payload["grade"] == "pass"
    assert payload["tier"] == "mini"
    assert payload["rows"]
    assert payload["checks"]
    assert payload["research_basis"]


def test_agent_scorecard_detects_role_regression() -> None:
    result = run_agent_scorecard(
        tier="mini",
        role_selector=lambda _prompt: (),
    )

    assert result.grade in {"warn", "fail"}
    assert result.pass_count < len(AGENT_SCORECARD_CASES)
    assert any(row.missing_roles for row in result.rows)


def test_agent_scorecard_markdown_writer(tmp_path: Path) -> None:
    out = tmp_path / "scorecard.md"
    write_agent_scorecard_md(run_agent_scorecard(tier="mini"), out)

    text = out.read_text()
    assert "# Agent System Scorecard" in text
    assert "| fast_math |" in text
    assert "Research Basis" in text


def test_agent_scorecard_cli_min_score_passes(capsys) -> None:
    rc = main(["--tier", "mini", "--score-only", "--min-score", "90"])

    assert rc == 0
    assert float(capsys.readouterr().out.strip()) == 100.0

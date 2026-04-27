from localsmartz.agent_role_doe import AGENT_ROLE_DOE_CASES, main, run_agent_role_doe
from localsmartz.routing import select_agent_roles


def test_select_agent_roles_returns_no_roles_for_fast_path():
    assert select_agent_roles("what is 2+2?") == ()


def test_select_agent_roles_current_data_uses_research_and_fact_check():
    assert select_agent_roles("what is the latest price of Apple stock?") == (
        "researcher",
        "fact_checker",
        "writer",
    )


def test_select_agent_roles_debug_uses_planner_and_analysis():
    assert select_agent_roles("debug why the macOS app is stuck launching") == (
        "planner",
        "researcher",
        "analyzer",
        "writer",
    )


def test_agent_role_doe_scores_current_classifier():
    payload = run_agent_role_doe(repetitions=1)

    assert payload["total_weight"] > 0
    assert payload["weighted_accuracy"] == 1.0
    assert len(payload["rows"]) == len(AGENT_ROLE_DOE_CASES)


def test_agent_role_doe_min_score_cli_passes(capsys):
    rc = main(["--repetitions", "1", "--score-only", "--min-score", "2500"])

    assert rc == 0
    float(capsys.readouterr().out.strip())

from localsmartz import server_timing
from localsmartz.benchmarking import RunMetrics


def test_run_server_timing_matrix_uses_representative_cases(monkeypatch, tmp_path):
    calls: list[str] = []

    def fake_measure(base_url, *, prompt, run_index, **_kwargs):
        calls.append(prompt)
        return RunMetrics(
            run_index=run_index,
            first_text_ms=run_index * 10,
            wall_duration_ms=run_index * 20,
        )

    monkeypatch.setattr(server_timing, "measure_research_request", fake_measure)

    payload = server_timing.run_server_timing_matrix(
        base_url="http://127.0.0.1:1",
        cwd=str(tmp_path),
        runs=2,
    )

    assert len(payload["cases"]) == len(server_timing.SERVER_TIMING_CASES)
    assert len(calls) == len(server_timing.SERVER_TIMING_CASES) * 2
    first = payload["cases"][0]
    assert first["runtime_ok"] is True
    assert first["summary"]["median_first_text_ms"] == 15.0


def test_server_timing_cli_with_existing_base_url(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        server_timing,
        "measure_research_request",
        lambda *_args, **kwargs: RunMetrics(
            run_index=kwargs["run_index"],
            first_text_ms=7,
            wall_duration_ms=9,
        ),
    )

    rc = server_timing.main([
        "--base-url",
        "http://127.0.0.1:1",
        "--cwd",
        str(tmp_path),
        "--limit",
        "1",
    ])

    assert rc == 0
    assert "fast_math" in capsys.readouterr().out

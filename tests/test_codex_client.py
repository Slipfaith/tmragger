import json

from core.codex_client import CodexVerifier, parse_codex_jsonl


def _events(*events: dict) -> str:
    return "\n".join(json.dumps(event) for event in events)


def test_parse_codex_jsonl_reads_verdict_and_usage():
    stdout = _events(
        {"type": "thread.started", "thread_id": "x"},
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": '{"verdict":"FAIL","issues":[],"summary":"bad"}'},
        },
        {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 7, "reasoning_output_tokens": 3}},
    )

    result = parse_codex_jsonl(stdout)

    assert result.verdict == "FAIL"
    assert result.summary == "bad"
    assert (result.prompt_tokens, result.completion_tokens, result.total_tokens) == (100, 10, 110)


def test_parse_codex_jsonl_error_is_reported_as_request_failure():
    stdout = _events({"type": "turn.failed", "error": {"message": "model not supported"}})

    result = parse_codex_jsonl(stdout, returncode=1)

    assert result.verdict == "WARN"
    assert result.summary == "Codex request failed"
    assert "model not supported" in result.issues[0].message


def test_build_command_passes_model_and_effort():
    verifier = CodexVerifier(model="gpt-6-luna", reasoning_effort="medium", codex_bin="codex")

    command = verifier.build_command()

    assert command[:2] == ["codex", "exec"]
    assert ["-m", "gpt-6-luna"] == command[command.index("-m") : command.index("-m") + 2]
    assert "model_reasoning_effort=medium" in command
    assert ["-s", "read-only"] == command[command.index("-s") : command.index("-s") + 2]


def test_cancel_kills_running_codex_call_and_refuses_new_ones(monkeypatch):
    import sys
    import threading
    import time

    from core.codex_client import CodexVerifier

    verifier = CodexVerifier(codex_bin="codex-test")
    monkeypatch.setattr(
        verifier,
        "build_command",
        lambda output_schema=False: [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    outcome: list[str] = []

    def call() -> None:
        try:
            verifier._run("prompt")
            outcome.append("finished")
        except RuntimeError as exc:
            outcome.append(str(exc))

    thread = threading.Thread(target=call)
    started = time.monotonic()
    thread.start()
    time.sleep(0.5)
    verifier.cancel()
    thread.join(timeout=15)

    assert not thread.is_alive()
    assert time.monotonic() - started < 15
    assert outcome == ["Codex verification cancelled"]
    try:
        verifier._run("again")
    except RuntimeError as exc:
        assert "cancelled" in str(exc)
    else:
        raise AssertionError("cancelled verifier must refuse new calls")

import json

from llm_wiki.harness_sessions import discover_harness_sessions
from llm_wiki.project import ProjectWiki
from llm_wiki.cli import main


def test_discover_claude_code_sessions_from_project_cwd(tmp_path):
    project = tmp_path / "demo-project"
    project.mkdir()
    root = tmp_path / ".claude-personal1"
    session_dir = root / "projects" / "-tmp-demo-project"
    session_dir.mkdir(parents=True)
    session_file = session_dir / "abc.jsonl"
    session_file.write_text(
        "\n".join([
            json.dumps({"type": "permission-mode", "sessionId": "abc"}),
            json.dumps({"type": "user", "timestamp": "2026-05-05T10:00:00Z", "cwd": str(project), "sessionId": "abc", "gitBranch": "main", "message": {"role": "user", "content": "Add project memory pages\nwith details"}}),
            json.dumps({"type": "assistant", "timestamp": "2026-05-05T10:01:00Z", "cwd": str(project), "sessionId": "abc", "message": {"role": "assistant", "content": [{"type": "text", "text": "Implemented it."}, {"type": "tool_use", "name": "Write", "input": {"file_path": str(project / "llm_wiki/site/sessions.py")}}]}}),
            json.dumps({"type": "attachment", "timestamp": "2026-05-05T10:02:00Z", "cwd": str(project), "sessionId": "abc", "attachment": {"type": "hook_success", "command": "pytest tests/test_harness_sessions.py -q"}}),
        ]) + "\n",
        encoding="utf-8",
    )

    sessions = discover_harness_sessions(project, [root], harnesses=["claude-code"])

    assert len(sessions) == 1
    session = sessions[0]
    assert session.harness == "claude-code"
    assert session.title == "Add project memory pages"
    assert session.branch == "main"
    assert session.message_count == 2
    assert session.tool_call_count == 2
    assert "Write" in session.tools_used
    assert "llm_wiki/site/sessions.py" in session.files_touched
    assert "pytest tests/test_harness_sessions.py -q" in session.commands_run


def test_discover_codex_sessions_from_session_meta_cwd(tmp_path):
    project = tmp_path / "demo-project"
    project.mkdir()
    root = tmp_path / ".codex-personal1"
    session_dir = root / "sessions" / "2026" / "05" / "05"
    session_dir.mkdir(parents=True)
    session_file = session_dir / "rollout-2026-05-05T11-00-00-abc.jsonl"
    session_file.write_text(
        "\n".join([
            json.dumps({"timestamp": "2026-05-05T11:00:00Z", "type": "session_meta", "payload": {"id": "codex-abc", "cwd": str(project), "model_provider": "openai"}}),
            json.dumps({"timestamp": "2026-05-05T11:00:01Z", "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Review graph UX"}]}}),
            json.dumps({"timestamp": "2026-05-05T11:00:02Z", "type": "response_item", "payload": {"type": "function_call", "name": "exec_command", "arguments": json.dumps({"cmd": "python3 -m pytest tests/ -q", "workdir": str(project)})}}),
            json.dumps({"timestamp": "2026-05-05T11:00:03Z", "type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Found issues in llm_wiki/site/js.py"}]}}),
        ]) + "\n",
        encoding="utf-8",
    )

    sessions = discover_harness_sessions(project, [root], harnesses=["codex"])

    assert len(sessions) == 1
    session = sessions[0]
    assert session.harness == "codex"
    assert session.title == "Review graph UX"
    assert session.message_count == 2
    assert session.tool_call_count == 1
    assert "exec_command" in session.tools_used
    assert "python3 -m pytest tests/ -q" in session.commands_run
    assert "llm_wiki/site/js.py" in session.files_touched


def test_cli_sessions_discover_imports_matching_roots(tmp_path, capsys):
    project = tmp_path / "demo-project"
    project.mkdir()
    ProjectWiki.init(project, name="demo_project")
    root = tmp_path / ".codex-personal1"
    session_dir = root / "sessions" / "2026" / "05" / "05"
    session_dir.mkdir(parents=True)
    (session_dir / "rollout-2026-05-05T11-00-00-abc.jsonl").write_text(
        json.dumps({"timestamp": "2026-05-05T11:00:00Z", "type": "session_meta", "payload": {"id": "codex-abc", "cwd": str(project)}}) + "\n"
        + json.dumps({"timestamp": "2026-05-05T11:00:01Z", "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Import this session"}]}}) + "\n",
        encoding="utf-8",
    )

    assert main(["project", "sessions", "discover", "--project", str(project), "--root", str(root), "--harness", "codex", "--import"]) == 0

    out = capsys.readouterr().out
    assert "Discovered harness sessions: 1" in out
    assert "Imported harness sessions: 1" in out
    assert (ProjectWiki.load(project).paths.harness_sessions / "codex").exists()

import json

from tesserae.harness_sessions import discover_harness_roots, discover_harness_sessions
from tesserae.project import ProjectWiki
from tesserae.cli import main


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
            json.dumps({"type": "assistant", "timestamp": "2026-05-05T10:01:00Z", "cwd": str(project), "sessionId": "abc", "message": {"role": "assistant", "content": [{"type": "text", "text": "Implemented it."}, {"type": "tool_use", "name": "Write", "input": {"file_path": str(project / "tesserae/site/sessions.py")}}]}}),
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
    assert "tesserae/site/sessions.py" in session.files_touched
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
            json.dumps({"timestamp": "2026-05-05T11:00:03Z", "type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Found issues in tesserae/site/js.py"}]}}),
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
    assert "tesserae/site/js.py" in session.files_touched


def test_discovers_dynamic_claude_and_codex_account_roots_without_fixed_names(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "demo-project"
    project.mkdir()
    claude_root = home / ".work-agent-alpha"
    (claude_root / "projects").mkdir(parents=True)
    codex_root = home / ".client-agent-beta"
    (codex_root / "sessions").mkdir(parents=True)
    ignored = home / ".claude-notes"
    ignored.mkdir(parents=True)

    roots = discover_harness_roots(home)

    assert claude_root in roots
    assert codex_root in roots
    assert ignored not in roots


def test_default_discovery_uses_dynamic_account_roots(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "demo-project"
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    root = home / ".client-agent-beta"
    session_dir = root / "sessions" / "2026" / "05" / "05"
    session_dir.mkdir(parents=True)
    (session_dir / "rollout-2026-05-05T11-00-00-abc.jsonl").write_text(
        json.dumps({"timestamp": "2026-05-05T11:00:00Z", "type": "session_meta", "payload": {"id": "codex-abc", "cwd": str(project)}}) + "\n"
        + json.dumps({"timestamp": "2026-05-05T11:00:01Z", "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Import dynamic root"}]}}) + "\n",
        encoding="utf-8",
    )

    sessions = discover_harness_sessions(project, roots=None, harnesses=["codex"])

    assert len(sessions) == 1
    assert sessions[0].title == "Import dynamic root"


def test_discovery_ignores_sessions_that_only_mention_project_path(tmp_path):
    project = tmp_path / "focused-project"
    other_project = tmp_path / "other-project"
    project.mkdir()
    other_project.mkdir()
    root = tmp_path / ".codex-any-account"
    session_dir = root / "sessions" / "2026" / "05" / "05"
    session_dir.mkdir(parents=True)
    (session_dir / "rollout-2026-05-05T11-00-00-other.jsonl").write_text(
        "\n".join([
            json.dumps({"timestamp": "2026-05-05T11:00:00Z", "type": "session_meta", "payload": {"id": "codex-other", "cwd": str(other_project)}}),
            json.dumps({"timestamp": "2026-05-05T11:00:01Z", "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": f"Compare with docs in {project}"}]}}),
            json.dumps({"timestamp": "2026-05-05T11:00:02Z", "type": "response_item", "payload": {"type": "function_call", "name": "exec_command", "arguments": json.dumps({"cmd": f"grep -R TODO {project}", "workdir": str(other_project)})}}),
        ]) + "\n",
        encoding="utf-8",
    )

    sessions = discover_harness_sessions(project, [root], harnesses=["codex"])

    assert sessions == []


def test_discovery_uses_plugged_project_root_not_neighbor_project(tmp_path):
    focused = tmp_path / "focused-project"
    neighbor = tmp_path / "neighbor-project"
    focused.mkdir()
    neighbor.mkdir()
    root = tmp_path / ".codex-any-account"
    session_dir = root / "sessions" / "2026" / "05" / "05"
    session_dir.mkdir(parents=True)
    (session_dir / "rollout-2026-05-05T11-00-00-focused.jsonl").write_text(
        json.dumps({"timestamp": "2026-05-05T11:00:00Z", "type": "session_meta", "payload": {"id": "codex-focused", "cwd": str(focused)}}) + "\n"
        + json.dumps({"timestamp": "2026-05-05T11:00:01Z", "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Focused project session"}]}}) + "\n",
        encoding="utf-8",
    )
    (session_dir / "rollout-2026-05-05T12-00-00-neighbor.jsonl").write_text(
        json.dumps({"timestamp": "2026-05-05T12:00:00Z", "type": "session_meta", "payload": {"id": "codex-neighbor", "cwd": str(neighbor)}}) + "\n"
        + json.dumps({"timestamp": "2026-05-05T12:00:01Z", "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Neighbor project session"}]}}) + "\n",
        encoding="utf-8",
    )

    sessions = discover_harness_sessions(focused, [root], harnesses=["codex"])

    assert [session.title for session in sessions] == ["Focused project session"]
    assert all(session.project_root == str(focused.resolve()) for session in sessions)


def test_claude_discovery_does_not_count_subagent_transcripts_as_sessions(tmp_path):
    project = tmp_path / "focused-project"
    project.mkdir()
    root = tmp_path / ".claude-any-account"
    session_dir = root / "projects" / str(project.resolve()).replace("/", "-")
    subagent_dir = session_dir / "parent-session" / "subagents"
    subagent_dir.mkdir(parents=True)
    (session_dir / "parent-session.jsonl").write_text(
        json.dumps({"type": "user", "timestamp": "2026-05-05T10:00:00Z", "cwd": str(project), "sessionId": "parent-session", "message": {"role": "user", "content": "Parent session"}}) + "\n",
        encoding="utf-8",
    )
    (subagent_dir / "agent-child.jsonl").write_text(
        json.dumps({"type": "user", "timestamp": "2026-05-05T10:01:00Z", "cwd": str(project), "sessionId": "parent-session", "message": {"role": "user", "content": "Child subagent session"}}) + "\n",
        encoding="utf-8",
    )

    sessions = discover_harness_sessions(project, [root], harnesses=["claude-code"])

    assert len(sessions) == 1
    assert sessions[0].title == "Parent session"
    assert "subagents" not in sessions[0].raw_transcript_path
    assert sessions[0].metadata["subagents"][0]["title"] == "Child subagent session"
    assert sessions[0].metadata["subagents"][0]["message_count"] == 1
    assert "subagents" in sessions[0].metadata["subagents"][0]["raw_transcript_path"]


def test_claude_project_directory_without_session_cwd_is_not_enough(tmp_path):
    project = tmp_path / "focused-project"
    project.mkdir()
    root = tmp_path / ".claude-any-account"
    session_dir = root / "projects" / str(project.resolve()).replace("/", "-")
    session_dir.mkdir(parents=True)
    (session_dir / "abc.jsonl").write_text(
        "\n".join([
            json.dumps({"type": "permission-mode", "sessionId": "abc"}),
            json.dumps({"type": "user", "timestamp": "2026-05-05T10:00:00Z", "sessionId": "abc", "message": {"role": "user", "content": "No cwd here"}}),
        ]) + "\n",
        encoding="utf-8",
    )

    sessions = discover_harness_sessions(project, [root], harnesses=["claude-code"])

    assert sessions == []


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

    assert main(["sessions", "discover", "--project", str(project), "--root", str(root), "--harness", "codex", "--import"]) == 0

    out = capsys.readouterr().out
    assert f"Project working directory: {project.resolve()}" in out
    assert "Project-attached harness sessions: 1" in out
    assert "codex: 1" in out
    assert "Imported harness sessions: 1" in out
    assert (ProjectWiki.load(project).paths.harness_sessions / "codex").exists()

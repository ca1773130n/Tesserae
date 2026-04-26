import os
import subprocess
import sys
import venv
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_editable_install_exposes_llm_wiki_console_command(tmp_path):
    env_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(env_dir)
    bin_dir = env_dir / ("Scripts" if os.name == "nt" else "bin")
    python = bin_dir / ("python.exe" if os.name == "nt" else "python")
    llm_wiki = bin_dir / ("llm_wiki.exe" if os.name == "nt" else "llm_wiki")

    upgrade = subprocess.run(
        [str(python), "-m", "pip", "install", "--upgrade", "pip"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

    install = subprocess.run(
        [str(python), "-m", "pip", "install", "-e", str(REPO_ROOT)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )

    assert install.returncode == 0, install.stdout + install.stderr
    result = subprocess.run(
        [str(llm_wiki), "project", "init", "--help"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "source-kind" in result.stdout


def test_install_script_has_curl_pipe_bash_contract_and_valid_syntax():
    script = REPO_ROOT / "scripts" / "install.sh"

    assert script.exists()
    text = script.read_text(encoding="utf-8")
    assert "curl -fsSL" in text
    assert "pip install -e" in text
    assert "llm_wiki" in text
    assert "--dir" in text
    assert "--no-venv" in text
    assert "--skip-shell-config" in text

    result = subprocess.run(
        ["bash", "-n", str(script)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr

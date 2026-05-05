from __future__ import annotations

from pathlib import Path

from lmit_wiki.config import load_config


ROOT = Path(__file__).resolve().parents[1]


def test_windows_config_template_is_localhost_and_schedules_tasks():
    cfg = load_config(ROOT / "config" / "wiki-only.windows.example.toml")

    assert cfg.wiki_runtime.serve_host == "127.0.0.1"
    assert cfg.wiki_runtime.serve_port == 8765
    assert cfg.wiki_ingest.source_dirs
    assert cfg.windows.task_schedule.enabled is False
    assert cfg.windows.task_schedule.ingest_interval_minutes == 60


def test_load_config_accepts_utf8_bom(tmp_path):
    config_path = tmp_path / "wiki-only.toml"
    config_path.write_text("\ufeff[wiki_runtime]\nserve_port = 9001\n", encoding="utf-8")

    cfg = load_config(config_path)

    assert cfg.wiki_runtime.serve_port == 9001


def test_pyinstaller_spec_builds_lmit_wiki_exe():
    text = (ROOT / "packaging" / "windows" / "lmit-wiki.spec").read_text(encoding="utf-8")

    assert "src" in text
    assert "lmit_wiki" in text
    assert 'name="lmit-wiki"' in text
    assert "wiki-only.windows.example.toml" in text


def test_inno_installer_defines_shortcuts_config_init_and_uninstall_cleanup():
    text = (ROOT / "packaging" / "windows" / "lmit-2-windows.iss").read_text(
        encoding="utf-8"
    )

    assert "LMIT-2 Wiki Console" in text
    assert 'Name: "{group}\\LMIT-2 Wiki Console"; Filename: "powershell.exe"' in text
    assert 'Name: "{autodesktop}\\LMIT-2 Wiki Console"; Filename: "powershell.exe"' in text
    assert 'IconFilename: "{app}\\lmit-wiki.exe"' in text
    assert "LMIT-2 CLI Help" in text
    assert "init-windows-install.ps1" in text
    assert "remove-scheduled-tasks.ps1" in text
    assert "GetKnowledgeBaseRoot" in text
    assert "GetRawSourceDir" in text
    assert "CreateInputDirPage" not in text
    assert "Flags: checkedonce" not in text
    assert "Flags: unchecked" in text
    assert "web-ui-guide.md" in text
    assert 'DestName: ".env.example"' in text
    assert "LMIT-2 Ingest Now" not in text
    assert "LMIT-2 Sync Now" not in text
    assert "LMIT-2 Lint Now" not in text


def test_windows_task_scripts_register_and_remove_scheduled_tasks():
    scripts_dir = ROOT / "packaging" / "windows" / "scripts"
    install_text = (scripts_dir / "install-scheduled-tasks.ps1").read_text(encoding="utf-8")
    remove_text = (scripts_dir / "remove-scheduled-tasks.ps1").read_text(encoding="utf-8")
    start_text = (scripts_dir / "start-console.ps1").read_text(encoding="utf-8")
    cmd_text = (scripts_dir / "start-console.cmd").read_text(encoding="utf-8")
    init_text = (scripts_dir / "init-windows-install.ps1").read_text(encoding="utf-8")
    common_text = (scripts_dir / "config-common.ps1").read_text(encoding="utf-8")

    assert "Register-ScheduledTask" in install_text
    assert "LMIT-2 Wiki Ingest" in install_text
    assert "LMIT-2 Wiki Sync" in install_text
    assert "LMIT-2 Wiki Lint" in install_text
    assert "Unregister-ScheduledTask" in remove_text
    assert "127.0.0.1:8765" in start_text
    assert "Ensure-LmitWikiConfig" in start_text
    assert "Start-Process" in start_text
    assert "RedirectStandardOutput $stdoutPath" in start_text
    assert "RedirectStandardError $stderrPath" in start_text
    assert "start-console.ps1" in cmd_text
    assert "UTF8Encoding($false)" in common_text
    assert "WriteAllText" in common_text
    assert "Select-LmitFolder" in common_text
    assert "FolderBrowserDialog" in common_text
    assert "broken-" in common_text
    assert "Copy-Item -LiteralPath $ConfigPath" in common_text
    assert "Invoke-LmitWikiInit" in init_text
    assert "[bool]$InstallTasks = $false" in init_text
    assert "if ($InstallTasks)" in init_text
    assert init_text.index("if ($InstallTasks)") < init_text.index("Write-LmitWikiConfig")

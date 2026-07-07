"""Regression tests for git_service hardening (SEC-003).

Verifies that:
* Hostile repo config (``core.fsmonitor``) cannot inject executables into git
  invocations. ``git status`` invokes fsmonitor, so this is the meaningful
  attack vector for the read-only commands the service runs.
* ``configure`` rejects ``project_root`` values that are not absolute, do not
  exist, or do not contain a ``.git`` entry.
"""

import subprocess
from pathlib import Path

from app.services.git_service import GitService


class TestGitServiceHardening:
    """Verify git invocations resist hostile repo config (SEC-003)."""

    def test_fsmonitor_not_executed(self, tmp_path: Path) -> None:
        """A hostile core.fsmonitor in repo config must not run during status."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        marker = tmp_path / "pwned"
        # Configure fsmonitor to create a marker file if it is executed.
        subprocess.run(
            ["git", "config", "core.fsmonitor", f"touch {marker}"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        service = GitService()
        # Without hardening, `git status` would invoke fsmonitor and create marker.
        result = service.get_status(repo_path=str(repo))
        assert not marker.exists(), "fsmonitor command ran — hardening failed"
        assert result is not None


class TestConfigureProjectRootValidation:
    """configure must validate project_root before storing (SEC-003)."""

    def test_rejects_nonexistent_path(self) -> None:
        service = GitService()
        service.configure(session_id="bad", project_root="/nonexistent/path/xyz")
        assert "bad" not in service._sessions  # pyright: ignore[reportPrivateUsage]

    def test_rejects_relative_path(self) -> None:
        service = GitService()
        service.configure(session_id="rel", project_root="relative/path")
        assert "rel" not in service._sessions  # pyright: ignore[reportPrivateUsage]

    def test_rejects_directory_without_git(self, tmp_path: Path) -> None:
        not_a_repo = tmp_path / "no-git"
        not_a_repo.mkdir()
        service = GitService()
        service.configure(session_id="no-git", project_root=str(not_a_repo))
        assert "no-git" not in service._sessions  # pyright: ignore[reportPrivateUsage]

    def test_accepts_valid_git_repo(self, tmp_path: Path) -> None:
        repo = tmp_path / "valid-repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        service = GitService()
        service.configure(session_id="good", project_root=str(repo))
        assert service._sessions.get("good") == str(repo.resolve())  # pyright: ignore[reportPrivateUsage]

    def test_none_project_root_still_clears_state(self) -> None:
        """configure(project_root=None) is the documented clear sentinel."""
        service = GitService()
        # Should not raise and should not store anything weird.
        service.configure(session_id="none-session", project_root=None)
        assert service._sessions.get("none-session") is None  # pyright: ignore[reportPrivateUsage]

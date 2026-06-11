"""Background update checker — queries the GitHub Releases API."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal


def _parse_version(tag: str) -> tuple[int, ...]:
    """Parse 'v1.2.3' or '1.2.3' into (1, 2, 3) for comparison."""
    tag = tag.lstrip("v").strip()
    try:
        return tuple(int(x) for x in tag.split("."))
    except ValueError:
        return (0,)


class UpdateChecker(QThread):
    """Fetches the latest GitHub release tag in the background.

    Signals
    -------
    result(latest_version, release_url)
        Emitted on success.  latest_version is a plain string like "1.2.3".
    error(message)
        Emitted when the check fails (network error, bad JSON, etc.).
    """

    result = Signal(str, str)   # latest_version, release_url
    error  = Signal(str)

    def __init__(self, repo: str, parent=None):
        """
        Parameters
        ----------
        repo : str
            "owner/repo" GitHub repository slug.
        """
        super().__init__(parent)
        self._repo = repo

    def run(self):
        import json
        import urllib.request
        import urllib.error

        url = f"https://api.github.com/repos/{self._repo}/releases/latest"
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "RehearsalRoom-UpdateChecker/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            tag         = data.get("tag_name", "")
            release_url = data.get("html_url", f"https://github.com/{self._repo}/releases")

            if not tag:
                self.error.emit("GitHub returned a release with no tag name.")
                return

            self.result.emit(tag.lstrip("v"), release_url)

        except urllib.error.URLError as exc:
            self.error.emit(f"Could not reach GitHub: {exc.reason}")
        except Exception as exc:
            self.error.emit(str(exc))

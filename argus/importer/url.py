"""Normalize GitHub HTTPS URLs and derive cache directory names."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})


@dataclass(frozen=True)
class GitHubRepoRef:
    """``owner/repo`` from an HTTPS GitHub URL."""

    original_url: str
    normalized_url: str
    owner: str
    repo: str

    @property
    def slug(self) -> str:
        return f"{self.owner}_{self.repo}"


def normalize_github_https(url: str) -> str:
    s = url.strip()
    if not s:
        raise ValueError("repo URL is empty")
    if s.endswith("/"):
        s = s[:-1]
    if s.endswith(".git"):
        s = s[:-4]
    return s


def parse_github_repo(url: str) -> GitHubRepoRef:
    """
    Parse ``https://github.com/owner/repo`` (``.git`` optional).

    Raises ``ValueError`` if the host is not GitHub or the path is not owner/repo.
    """
    raw = normalize_github_https(url)
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"expected http(s) URL, got {url!r}")
    host = (parsed.hostname or "").lower()
    if host not in _GITHUB_HOSTS:
        raise ValueError(f"expected github.com host, got {host!r}")
    path = parsed.path.strip("/")
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        raise ValueError(f"expected /owner/repo in path, got {path!r}")
    owner, repo = parts[0], parts[1]
    if not re.match(r"^[A-Za-z0-9_.-]+$", owner) or not re.match(r"^[A-Za-z0-9_.-]+$", repo):
        raise ValueError(f"suspicious owner/repo segment: {owner!r}/{repo!r}")
    normalized = f"https://github.com/{owner}/{repo}"
    return GitHubRepoRef(original_url=url.strip(), normalized_url=normalized, owner=owner, repo=repo)

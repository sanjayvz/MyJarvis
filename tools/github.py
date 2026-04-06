"""
title: GITHUB
description: Ultimate GitHub Agent: Search, read files, list PRs, create issues, view releases, and comment on repos.
version: 2.1.0
license: MIT
"""

import json
import base64
import requests
import re
from typing import Any, Awaitable, Callable, Dict, Optional
from pydantic import BaseModel, Field


class EventEmitter:
    def __init__(self, event_emitter: Callable[[dict], Awaitable[None]]):
        self.event_emitter = event_emitter

    async def emit_status(self, description: str, done: bool, error: bool = False):
        if self.event_emitter:
            await self.event_emitter(
                {
                    "data": {
                        "description": f"{'❌' if done and error else '✅' if done else '🔎'} {description}",
                        "status": "complete" if done else "in_progress",
                        "done": done,
                    },
                    "type": "status",
                }
            )

    async def emit_source(self, name: str, url: str, content: str, html: bool = False):
        if self.event_emitter:
            await self.event_emitter(
                {
                    "type": "citation",
                    "data": {
                        "document": [content],
                        "metadata": [{"source": url, "html": html}],
                        "source": {"name": name, "url": url},
                    },
                }
            )


class GitHubApiError(Exception):
    pass


class GitHubClient:
    def __init__(self, token: str, base_url: str = "https://api.github.com"):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"token {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _request(self, method: str, path: str, params=None, data=None):
        url = f"{self.base_url}/{path.lstrip('/')}"

        response = requests.request(
            method, url, params=params, json=data, headers=self.headers
        )

        if response.status_code >= 400:
            try:
                error_details = response.json().get("message", response.text)
            except Exception:
                error_details = response.text
            raise GitHubApiError(
                f"API Error ({response.status_code}) on {method} {path}: {error_details}"
            )

        return response.json() if response.status_code != 204 else {}


class Tools:
    def __init__(self):
        self.valves = self.Valves()

    class Valves(BaseModel):
        github_token: str = Field("", description="GitHub Personal Access Token (PAT)")
        github_url: str = Field(
            "https://api.github.com",
            description="Base API URL. Change only if using GitHub Enterprise Server.",
        )
        default_org: str = Field(
            "",
            description="Optional: Default organization or owner name (e.g., 'elastic')",
        )

    def _get_client(self) -> GitHubClient:
        if not self.valves.github_token:
            raise GitHubApiError(
                "GitHub token not configured. Set it in tool settings."
            )
        return GitHubClient(self.valves.github_token, self.valves.github_url)

    def _parse_repo(self, repo_name: str) -> str:
        """Helper to ensure repo name is in 'owner/repo' format."""
        if "/" not in repo_name and self.valves.default_org:
            return f"{self.valves.default_org}/{repo_name}"
        return repo_name

    async def get_open_issues(
        self,
        repo_name: str,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Fetches open issues from a specific GitHub repository.
        repo_name: MUST be in the format 'owner/repo' (e.g., 'elastic/ElasticGPT').
        """
        repo_name = self._parse_repo(repo_name)
        query = f"repo:{repo_name} is:issue is:open"

        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(
            f"Routing request to fetch open issues for {repo_name}...", False
        )

        return await self.search_github_issues(query, __event_emitter__)

    async def search_and_read_by_title(
        self,
        repo_name: str,
        keywords: str,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Search for a GitHub issue by its title or keywords, and automatically read its full details.
        Use this when the user gives you words to look for but DOES NOT give you the exact issue number.
        """
        emitter = EventEmitter(__event_emitter__)
        repo_name = self._parse_repo(repo_name)
        await emitter.emit_status(f"Searching {repo_name} for '{keywords}'...", False)

        # Strip special characters that break GitHub's search API
        safe_keywords = re.sub(r"[^\w\s]", " ", keywords).strip()
        query = f"repo:{repo_name} {safe_keywords} is:issue"

        try:
            client = self._get_client()
            res = client._request(
                "GET", "search/issues", params={"q": query, "per_page": 1}
            )

            items = res.get("items", [])
            if not items:
                await emitter.emit_status(
                    f"No match found for '{keywords}'", True, True
                )
                return f"Could not find any issues in {repo_name} matching the keywords: {keywords}"

            issue_number = items[0].get("number")
            await emitter.emit_status(
                f"Found match: #{issue_number}. Fetching details...", False
            )

            return await self.get_github_issue_or_pr(
                repo_name, issue_number, __event_emitter__
            )

        except Exception as e:
            await emitter.emit_status(f"Search by words failed: {e}", True, True)
            return f"Error: {e}"

    async def search_github_issues(
        self, query: str, __event_emitter__: Callable[[dict], Awaitable[None]] = None
    ) -> str:
        """
        Search for GitHub Issues and Pull Requests.
        CRITICAL: If using the 'repo:' qualifier, you MUST include the owner/organization.
        CRITICAL: You MUST include 'is:issue' or 'is:pull-request' in the query.
        Examples: 'repo:elastic/kibana is:pr is:open', 'repo:elastic/ElasticGPT is:issue is:open'
        """
        if self.valves.default_org:
            query = re.sub(
                r"repo:([^/\s]+)(?=\s|$)", rf"repo:{self.valves.default_org}/\1", query
            )

        if not any(
            marker in query
            for marker in [
                "is:issue",
                "is:pr",
                "is:pull-request",
                "type:issue",
                "type:pr",
            ]
        ):
            query += " is:issue"

        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(f"Searching GitHub: {query}", False)

        try:
            client = self._get_client()
            res = client._request(
                "GET", "search/issues", params={"q": query, "per_page": 10}
            )

            output = [
                {
                    "number": item.get("number"),
                    "title": item.get("title"),
                    "body": item.get("body", "No description provided.")[:3000],
                    "state": item.get("state"),
                    "type": "Pull Request" if "pull_request" in item else "Issue",
                    "repo": item.get("repository_url", "").split("repos/")[-1],
                    "html_url": item.get("html_url"),
                }
                for item in res.get("items", [])
            ]

            await emitter.emit_status(f"Found {len(output)} results", True)
            return json.dumps(output, indent=2)
        except Exception as e:
            await emitter.emit_status(f"Search failed: {e}", True, True)
            return f"Error: {e}"

    async def get_github_issue_or_pr(
        self,
        repo_name: str,
        issue_number: int,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Read the full details, description, and status of a specific GitHub Issue or Pull Request.
        repo_name MUST be in the format 'owner/repo' (e.g., 'elastic/kibana').
        """
        repo_name = self._parse_repo(repo_name)
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(f"Fetching #{issue_number} from {repo_name}", False)
        try:
            client = self._get_client()
            res = client._request("GET", f"repos/{repo_name}/issues/{issue_number}")

            output = {
                "number": res.get("number"),
                "title": res.get("title"),
                "state": res.get("state"),
                "author": res.get("user", {}).get("login"),
                "body": res.get("body", "No description provided."),
                "html_url": res.get("html_url"),
            }

            if "pull_request" in res:
                output["type"] = "Pull Request"
                pr_data = client._request(
                    "GET", f"repos/{repo_name}/pulls/{issue_number}"
                )
                output["merged"] = pr_data.get("merged", False)
                output["mergeable"] = pr_data.get("mergeable", "Unknown")
            else:
                output["type"] = "Issue"

            await emitter.emit_status(f"Loaded {output['type']} #{issue_number}", True)
            await emitter.emit_source(
                output["title"], output["html_url"], output["body"], False
            )
            return json.dumps(output, indent=2)
        except Exception as e:
            await emitter.emit_status(f"Failed to fetch: {e}", True, True)
            return f"Error: {e}"

    async def list_pull_requests(
        self,
        repo_name: str,
        state: str = "open",
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        List pull requests in a repository.
        repo_name MUST be in the format 'owner/repo'. state can be 'open', 'closed', or 'all'.
        """
        repo_name = self._parse_repo(repo_name)
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(f"Fetching {state} PRs for {repo_name}", False)
        try:
            client = self._get_client()
            res = client._request(
                "GET",
                f"repos/{repo_name}/pulls",
                params={"state": state, "per_page": 10},
            )

            output = [
                {
                    "number": pr.get("number"),
                    "title": pr.get("title"),
                    "author": pr.get("user", {}).get("login"),
                    "state": pr.get("state"),
                    "created_at": pr.get("created_at"),
                    "url": pr.get("html_url"),
                }
                for pr in res
            ]
            await emitter.emit_status(f"Found {len(output)} {state} PRs", True)
            return json.dumps(output, indent=2)
        except Exception as e:
            await emitter.emit_status(f"Failed to fetch PRs: {e}", True, True)
            return f"Error: {e}"

    async def get_file_content(
        self,
        repo_name: str,
        file_path: str,
        ref: str = "",
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Read the contents of a specific file in a GitHub repository.
        repo_name MUST be 'owner/repo'. file_path is the path (e.g., 'src/main.py' or 'package.json').
        ref is optional (branch name, tag, or commit SHA).
        """
        repo_name = self._parse_repo(repo_name)
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(f"Reading {file_path} from {repo_name}", False)
        try:
            client = self._get_client()
            params = {"ref": ref} if ref else {}
            res = client._request(
                "GET", f"repos/{repo_name}/contents/{file_path}", params=params
            )

            if isinstance(res, list):
                await emitter.emit_status(
                    "Target is a directory, not a file", True, True
                )
                return f"Error: '{file_path}' is a directory. You must specify a file path."

            if res.get("encoding") == "base64":
                content = base64.b64decode(res.get("content", "")).decode("utf-8")
                await emitter.emit_status(f"Read {file_path} successfully", True)
                await emitter.emit_source(
                    file_path, res.get("html_url"), content, False
                )

                preview = (
                    content[:1500] + "\n...[Truncated for context]"
                    if len(content) > 1500
                    else content
                )
                return json.dumps(
                    {
                        "file": file_path,
                        "url": res.get("html_url"),
                        "content_preview": preview,
                    },
                    indent=2,
                )
            else:
                return f"Error: Unsupported encoding {res.get('encoding')}."
        except Exception as e:
            await emitter.emit_status(f"Failed to read file: {e}", True, True)
            return f"Error: {e}"

    async def get_github_releases(
        self,
        repo_name: str,
        limit: int = 3,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Fetch the latest release notes and version tags from a GitHub repository.
        repo_name MUST be in the format 'owner/repo' (e.g., 'elastic/kibana').
        """
        repo_name = self._parse_repo(repo_name)
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(f"Fetching releases for {repo_name}", False)
        try:
            client = self._get_client()
            res = client._request(
                "GET", f"repos/{repo_name}/releases", params={"per_page": limit}
            )

            if not res:
                await emitter.emit_status(f"No releases found in {repo_name}", True)
                return f"No releases found for the repository {repo_name}."

            output = []
            for release in res:
                output.append(
                    {
                        "version_tag": release.get("tag_name"),
                        "title": release.get("name"),
                        "published_at": release.get("published_at"),
                        "author": release.get("author", {}).get("login"),
                        "release_notes": release.get(
                            "body", "No release notes provided."
                        ),
                        "url": release.get("html_url"),
                    }
                )

            await emitter.emit_status(f"Loaded {len(output)} recent releases", True)
            if output:
                await emitter.emit_source(
                    f"{repo_name} {output[0]['version_tag']}",
                    output[0]["url"],
                    output[0]["release_notes"],
                    False,
                )

            return json.dumps(output, indent=2)
        except Exception as e:
            await emitter.emit_status(f"Failed to fetch releases: {e}", True, True)
            return f"Error: {e}"

    async def create_github_issue(
        self,
        repo_name: str,
        title: str,
        body: str,
        labels: str = "",
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Create a new Issue in a specific GitHub repository.
        repo_name MUST be in the format 'owner/repo'.
        Labels should be comma-separated (e.g., 'bug, enhancement').
        """
        repo_name = self._parse_repo(repo_name)
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(f"Creating issue in {repo_name}", False)
        try:
            client = self._get_client()

            payload = {"title": title, "body": body}
            if labels:
                payload["labels"] = [
                    label.strip() for label in labels.split(",") if label.strip()
                ]

            res = client._request("POST", f"repos/{repo_name}/issues", data=payload)

            await emitter.emit_status(f"Created Issue #{res.get('number')}", True)
            return json.dumps(
                {
                    "number": res.get("number"),
                    "title": res.get("title"),
                    "html_url": res.get("html_url"),
                },
                indent=2,
            )
        except Exception as e:
            await emitter.emit_status(f"Failed to create issue: {e}", True, True)
            return f"Error: {e}"

    async def add_github_comment(
        self,
        repo_name: str,
        issue_number: int,
        comment: str,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Add a comment to an existing GitHub Issue or Pull Request.
        repo_name MUST be in the format 'owner/repo'.
        """
        repo_name = self._parse_repo(repo_name)
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(
            f"Adding comment to #{issue_number} in {repo_name}", False
        )
        try:
            client = self._get_client()
            res = client._request(
                "POST",
                f"repos/{repo_name}/issues/{issue_number}/comments",
                data={"body": comment},
            )

            await emitter.emit_status("Comment added successfully", True)
            return json.dumps(
                {"comment_id": res.get("id"), "html_url": res.get("html_url")}, indent=2
            )
        except Exception as e:
            await emitter.emit_status(f"Failed to add comment: {e}", True, True)
            return f"Error: {e}"

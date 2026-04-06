"""
title: CONFLUENCE
description: Read, search, create, update Confluence pages, and view comments/history. (Delete disabled).
version: 1.2.1
license: MIT
"""

import json
import requests
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


class ConfluenceApiError(Exception):
    pass


class ConfluenceClient:
    def __init__(self, username: str, password: str, base_url: str, pat: str = ""):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.pat = pat
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.pat:
            self.headers["Authorization"] = f"Bearer {self.pat}"

    def _get_auth(self):
        return None if self.pat else (self.username, self.password)

    def _request(self, method: str, path: str, params=None, data=None):
        url = f"{self.base_url}/wiki/rest/api/{path}"

        response = requests.request(
            method,
            url,
            params=params,
            json=data,
            headers=self.headers,
            auth=self._get_auth(),
        )

        if response.status_code >= 400:
            try:
                error_details = json.dumps(response.json())
            except Exception:
                error_details = response.text
            raise ConfluenceApiError(
                f"API Error ({response.status_code}) on {method} {path}: {error_details}"
            )

        return response.json() if response.status_code != 204 else {}


class Tools:
    def __init__(self):
        self.valves = self.Valves()

    class Valves(BaseModel):
        base_url: str = Field(
            "", description="Atlassian base URL (e.g. https://yourteam.atlassian.net)"
        )
        username: str = Field("", description="Atlassian username or email")
        password: str = Field("", description="Atlassian API token or password")
        pat: str = Field(
            "", description="Personal Access Token (if using Server/Data Center)"
        )

    def _get_client(self) -> ConfluenceClient:
        if not self.valves.base_url:
            raise ConfluenceApiError("Confluence base URL not configured.")
        return ConfluenceClient(
            self.valves.username,
            self.valves.password,
            self.valves.base_url,
            self.valves.pat,
        )

    async def search_confluence(
        self, cql: str, __event_emitter__: Callable[[dict], Awaitable[None]] = None
    ) -> str:
        """
        Search Confluence using CQL (Confluence Query Language).
        Examples: 'text ~ "project plan"', 'space = "ENG" AND type = "page"'
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(f"Searching Confluence: {cql}", False)
        try:
            client = self._get_client()
            res = client._request(
                "GET", "content/search", params={"cql": cql, "limit": 10}
            )

            output = [
                {
                    "id": page.get("id"),
                    "title": page.get("title"),
                    "type": page.get("type"),
                    "space": (page.get("space") or {}).get("key", "Unknown"),
                    "link": f"{client.base_url}/wiki{page.get('_links', {}).get('webui', '')}",
                }
                for page in res.get("results", [])
            ]

            await emitter.emit_status(f"Found {len(output)} Confluence pages", True)
            return json.dumps(output, indent=2)
        except Exception as e:
            await emitter.emit_status(f"Search failed: {e}", True, True)
            return f"Error: {e}"

    async def read_confluence_page(
        self, page_id: str, __event_emitter__: Callable[[dict], Awaitable[None]] = None
    ) -> str:
        """
        Read the content of a specific Confluence page using its page_id.
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(f"Reading page ID: {page_id}", False)
        try:
            client = self._get_client()
            res = client._request(
                "GET",
                f"content/{page_id}",
                params={"expand": "body.storage,version,space"},
            )

            title = res.get("title", "Untitled")
            content_html = (
                res.get("body", {}).get("storage", {}).get("value", "No content found.")
            )
            version = res.get("version", {}).get("number", 1)
            space_key = res.get("space", {}).get("key", "Unknown")
            link = f"{client.base_url}/wiki{res.get('_links', {}).get('webui', '')}"

            output = {
                "id": page_id,
                "title": title,
                "space": space_key,
                "version": version,
                "link": link,
                "content": content_html,
            }

            await emitter.emit_status(f"Successfully read page: {title}", True)
            await emitter.emit_source(title, link, content_html, True)
            return json.dumps(output, indent=2)
        except Exception as e:
            await emitter.emit_status(f"Failed to read page: {e}", True, True)
            return f"Error: {e}"

    async def get_confluence_comments(
        self, page_id: str, __event_emitter__: Callable[[dict], Awaitable[None]] = None
    ) -> str:
        """
        Fetch all comments from a specific Confluence page.
        Use this to read team feedback or discussions on a document.
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(f"Fetching comments for page {page_id}...", False)
        try:
            client = self._get_client()
            res = client._request(
                "GET",
                f"content/{page_id}/child/comment",
                params={"expand": "body.view,version"},
            )

            comments = []
            for c in res.get("results", []):
                author = (
                    c.get("version", {}).get("by", {}).get("displayName", "Unknown")
                )
                created = c.get("version", {}).get("when", "")
                body = c.get("body", {}).get("view", {}).get("value", "")
                comments.append({"author": author, "date": created, "comment": body})

            if not comments:
                await emitter.emit_status("No comments found.", True)
                return "This page has no comments."

            await emitter.emit_status(f"Found {len(comments)} comments", True)
            return json.dumps(comments, indent=2)
        except Exception as e:
            await emitter.emit_status(f"Failed to fetch comments: {e}", True, True)
            return f"Error: {e}"

    async def get_confluence_history(
        self,
        page_id: str,
        limit: int = 5,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Fetch the version history and recent updates of a Confluence page.
        Use this to see who recently edited the page and when.
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(
            f"Checking update history for page {page_id}...", False
        )
        try:
            client = self._get_client()
            res = client._request(
                "GET",
                f"content/{page_id}/version",
                params={"limit": limit, "expand": "collaborators"},
            )

            versions = []
            for v in res.get("results", []):
                versions.append(
                    {
                        "version": v.get("number"),
                        "author": v.get("by", {}).get("displayName", "Unknown"),
                        "update_message": v.get("message", "No message provided"),
                        "date": v.get("when", ""),
                    }
                )

            await emitter.emit_status(f"Found {len(versions)} recent versions", True)
            return json.dumps(versions, indent=2)
        except Exception as e:
            await emitter.emit_status(f"Failed to fetch history: {e}", True, True)
            return f"Error: {e}"

    async def create_confluence_page(
        self,
        space_key: str,
        title: str,
        content_html: str,
        parent_page_id: str = "",
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """

        Create a new Confluence page.

        RULES:
        1. space_key MUST be the exact short-code used in the Confluence URL.
        2. content_html MUST be fully written, valid HTML (e.g., <h1>, <p>, <ul>, <li>). NEVER use Markdown.
        3. CRITICAL DATA RULE: Never pass raw variables, JSON, or placeholders like '{tickets}' or '[Insert Data]' into content_html. If you fetched data from Jira or GitHub, you MUST parse that data and write it out as a complete HTML string (e.g., '<ul><li>INT-123 - Summary</li></ul>') BEFORE calling this tool.
        """

        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(
            f"Creating page '{title}' in space '{space_key}'", False
        )
        try:
            # 1. Catch lazy LLM empty strings
            if not content_html or len(content_html.strip()) == 0:
                await emitter.emit_status(
                    "Error: LLM provided empty content", True, True
                )
                return "Error: You tried to create a page with empty content. You MUST generate the HTML content."

            # 2. Safety wrapper for plain text / sloppy formatting (f-string fix applied)
            safe_html = content_html
            if not safe_html.strip().startswith("<"):
                safe_html = safe_html.replace("\n", "<br/>")
                safe_html = f"<p>{safe_html}</p>"

            client = self._get_client()

            data = {
                "type": "page",
                "title": title,
                "space": {"key": space_key},
                "body": {"storage": {"value": safe_html, "representation": "storage"}},
            }

            if parent_page_id:
                data["ancestors"] = [{"id": parent_page_id}]

            res = client._request("POST", "content", data=data)
            link = f"{client.base_url}/wiki{res.get('_links', {}).get('webui', '')}"

            await emitter.emit_status(f"Created page: {title}", True)
            return json.dumps(
                {
                    "message": "Page created successfully",
                    "id": res.get("id"),
                    "link": link,
                },
                indent=2,
            )
        except Exception as e:
            await emitter.emit_status(f"Failed to create page: {e}", True, True)
            return f"Error: {e}"

    async def update_confluence_page(
        self,
        page_id: str,
        title: str,
        content_html: str,
        update_message: str = "Updated via AI Agent",
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Modify an existing Confluence page.
        CRITICAL: content_html MUST be valid HTML (e.g. <h1>, <p>, <ul>, <li>).
        NEVER use Markdown (like # or **). Confluence will silently delete the content if you use Markdown.
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(f"Updating page ID {page_id}", False)
        try:
            # 1. Catch lazy LLM empty strings
            if not content_html or len(content_html.strip()) == 0:
                await emitter.emit_status(
                    "Error: LLM provided empty content", True, True
                )
                return "Error: You tried to update a page with empty content. You MUST generate the full HTML content."

            # 2. Safety wrapper for plain text (f-string fix applied)
            safe_html = content_html
            if not safe_html.strip().startswith("<"):
                safe_html = safe_html.replace("\n", "<br/>")
                safe_html = f"<p>{safe_html}</p>"

            client = self._get_client()

            current_page = client._request(
                "GET", f"content/{page_id}", params={"expand": "version,space"}
            )
            current_version = current_page.get("version", {}).get("number", 1)
            space_key = current_page.get("space", {}).get("key")

            data = {
                "id": page_id,
                "type": "page",
                "title": title,
                "space": {"key": space_key},
                "body": {"storage": {"value": safe_html, "representation": "storage"}},
                "version": {"number": current_version + 1, "message": update_message},
            }

            res = client._request("PUT", f"content/{page_id}", data=data)
            link = f"{client.base_url}/wiki{res.get('_links', {}).get('webui', '')}"

            await emitter.emit_status(
                f"Successfully updated page to v{current_version + 1}", True
            )
            return json.dumps(
                {
                    "message": "Page updated successfully",
                    "new_version": current_version + 1,
                    "link": link,
                },
                indent=2,
            )
        except Exception as e:
            await emitter.emit_status(f"Failed to update page: {e}", True, True)
            return f"Error: {e}"

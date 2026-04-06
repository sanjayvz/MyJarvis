"""
title: JIRA
description: Ultimate Jira management: Create structured Stories, find stale tickets, transition issues, and manage sprints/comments.
version: 3.1.0
license: MIT
"""

import json
import requests
from typing import Any, Awaitable, Callable, Dict, List, Optional
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


class JiraApiError(Exception):
    pass


class JiraClient:
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

    # 🚀 FIX: Added api_version parameter to allow dynamic switching
    def _request(
        self,
        method: str,
        path: str,
        params=None,
        data=None,
        is_agile=False,
        api_version="2",
    ):
        # We default to api/2 to prevent Atlassian Document Format crashes on creations/updates
        api_type = "agile/1.0" if is_agile else f"api/{api_version}"
        url = f"{self.base_url}/rest/{api_type}/{path}"

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
            raise JiraApiError(
                f"API Error ({response.status_code}) on {method} {path}: {error_details}"
            )

        return response.json() if response.status_code != 204 else {}

    def get_full_issue(self, issue_id: str):
        return self._request(
            "GET",
            f"issue/{issue_id}",
            params={"expand": "renderedFields,names,operations,transitions"},
        )

    def get_sprint_report(self, board_id: int):
        sprints = self._request(
            "GET", f"board/{board_id}/sprint", params={"state": "active"}, is_agile=True
        )
        if not sprints.get("values"):
            return {"error": f"No active sprint found for board {board_id}."}

        sprint = sprints["values"][0]
        issues = self._request(
            "GET",
            f"sprint/{sprint['id']}/issue",
            params={"maxResults": 100},
            is_agile=True,
        )
        return {"sprint": sprint, "issues": issues.get("issues", [])}


class Tools:
    def __init__(self):
        self.valves = self.Valves()

    class Valves(BaseModel):
        base_url: str = Field(
            "", description="Jira base URL (e.g. https://yourteam.atlassian.net)"
        )
        username: str = Field(
            "", description="Jira username or email (leave empty if using PAT)"
        )
        password: str = Field(
            "", description="Jira API token or password (leave empty if using PAT)"
        )
        pat: str = Field(
            "", description="Personal Access Token (alternative to username/password)"
        )

    def _get_client(self) -> JiraClient:
        if not self.valves.base_url:
            raise JiraApiError("Jira base URL not configured. Set it in tool settings.")
        if not self.valves.pat and not (self.valves.username and self.valves.password):
            raise JiraApiError(
                "Jira credentials not configured. Set username+token or PAT in tool settings."
            )
        return JiraClient(
            self.valves.username,
            self.valves.password,
            self.valves.base_url,
            self.valves.pat,
        )

    async def search_jira(
        self, jql: str, __event_emitter__: Callable[[dict], Awaitable[None]] = None
    ) -> str:
        """Search Jira using JQL. Use this for complex filtering like 'project = X AND assignee is EMPTY'."""
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(f"Executing JQL: {jql}", False)
        try:
            client = self._get_client()
            # 🚀 FIX: Explicitly calling api/3 and search/jql for this specific method
            res = client._request(
                "GET",
                "search/jql",
                params={
                    "jql": jql,
                    "maxResults": 50,
                    "fields": "summary,status,assignee",
                },
                api_version="3",
            )

            output = [
                {
                    "key": i.get("key", "Unknown"),
                    "summary": (i.get("fields") or {}).get("summary", "No summary"),
                    "status": ((i.get("fields") or {}).get("status") or {}).get(
                        "name", "Unknown"
                    ),
                }
                for i in res.get("issues", [])
            ]
            await emitter.emit_status(f"Found {len(output)} results", True)
            return json.dumps(output, indent=2)
        except Exception as e:
            await emitter.emit_status(f"Search failed: {e}", True, True)
            return f"Error: {e}"

    async def get_stale_sprint_issues(
        self,
        board_id: int,
        days_stale: int = 7,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Find issues in the active sprint that haven't been updated in a specific number of days.
        Useful for standups and finding blocked tickets.
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(
            f"Finding tickets stale for {days_stale}+ days...", False
        )
        try:
            client = self._get_client()

            sprints = client._request(
                "GET",
                f"board/{board_id}/sprint",
                params={"state": "active"},
                is_agile=True,
            )
            if not sprints.get("values"):
                return "No active sprint found."

            sprint = sprints["values"][0]
            sprint_id = sprint["id"]

            jql = f"sprint = {sprint_id} AND updated <= -{days_stale}d"
            # 🚀 FIX: Explicitly calling api/3 and search/jql for this specific method
            res = client._request(
                "GET",
                "search/jql",
                params={
                    "jql": jql,
                    "maxResults": 50,
                    "fields": "summary,status,assignee",
                },
                api_version="3",
            )

            issues = res.get("issues", [])
            if not issues:
                await emitter.emit_status("No stale tickets found! 🎉", True)
                return f"All tickets in '{sprint['name']}' have been updated in the last {days_stale} days."

            output = {
                "sprint": sprint["name"],
                "stale_count": len(issues),
                "stale_issues": [
                    {
                        "key": i.get("key", "Unknown"),
                        "summary": (i.get("fields") or {}).get("summary", "No summary"),
                        "assignee": ((i.get("fields") or {}).get("assignee") or {}).get(
                            "displayName", "Unassigned"
                        ),
                    }
                    for i in issues
                ],
            }

            await emitter.emit_status(f"Found {len(issues)} stale tickets", True)
            return json.dumps(output, indent=2)

        except Exception as e:
            await emitter.emit_status(f"Error: {e}", True, True)
            return f"Error: {e}"

    async def get_sprint_analysis(
        self, board_id: int, __event_emitter__: Callable[[dict], Awaitable[None]] = None
    ) -> str:
        """
        Retrieves the active sprint, its goal, and all issues. Use this to summarize
        current team progress or identify blockers.
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(f"Analyzing Board {board_id}...", False)
        try:
            client = self._get_client()
            data = client.get_sprint_report(board_id)
            if "error" in data:
                await emitter.emit_status(data["error"], True, True)
                return data["error"]

            summary = {
                "sprint_name": data["sprint"]["name"],
                "goal": data["sprint"].get("goal"),
                "end_date": data["sprint"].get("endDate"),
                "issue_count": len(data["issues"]),
                "issues": [
                    {
                        "key": i.get("key", "Unknown"),
                        "summary": (i.get("fields") or {}).get("summary", "No summary"),
                        "status": ((i.get("fields") or {}).get("status") or {}).get(
                            "name", "Unknown"
                        ),
                    }
                    for i in data["issues"]
                ],
            }
            await emitter.emit_status("Analysis complete", True)
            return json.dumps(summary, indent=2)
        except Exception as e:
            await emitter.emit_status(str(e), True, True)
            return f"Error: {e}"

    async def get_issue_details(
        self,
        issue_key: str,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """Get everything about an issue: description, status, full fields, and links."""
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(f"Deep dive: {issue_key}", False)
        try:
            client = self._get_client()
            issue = client.get_full_issue(issue_key)

            simplified = {
                "key": issue.get("key", "Unknown"),
                "summary": (issue.get("fields") or {}).get("summary", ""),
                "description": (issue.get("fields") or {}).get(
                    "description", "No description"
                ),
                "status": ((issue.get("fields") or {}).get("status") or {}).get(
                    "name", "Unknown"
                ),
                "assignee": ((issue.get("fields") or {}).get("assignee") or {}).get(
                    "displayName", "Unassigned"
                ),
                "priority": ((issue.get("fields") or {}).get("priority") or {}).get(
                    "name", "None"
                ),
                "subtasks": [
                    s.get("key")
                    for s in (issue.get("fields") or {}).get("subtasks", [])
                ],
                "links": [
                    l.get("outwardIssue", {}).get("key")
                    or l.get("inwardIssue", {}).get("key")
                    for l in (issue.get("fields") or {}).get("issuelinks", [])
                ],
            }
            await emitter.emit_status(f"Loaded {issue_key}", True)
            await emitter.emit_source(
                simplified["summary"],
                f"{client.base_url}/browse/{issue_key}",
                simplified["description"],
                True,
            )
            return json.dumps(simplified, indent=2)
        except Exception as e:
            await emitter.emit_status(f"Failed to get issue details: {e}", True, True)
            return f"Error: {e}"

    async def manage_comments(
        self,
        issue_id: str,
        action: str,
        body: str = "",
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Manage comments on an issue. Actions: 'get_all' (returns history), 'add' (requires body).
        """
        emitter = EventEmitter(__event_emitter__)
        client = self._get_client()
        try:
            if action == "get_all":
                await emitter.emit_status(f"Fetching history for {issue_id}", False)
                res = client._request("GET", f"issue/{issue_id}/comment")
                comments = [
                    {
                        "author": c.get("author", {}).get("displayName", "Unknown"),
                        "created": c.get("created", ""),
                        "body": c.get("body", ""),
                    }
                    for c in res.get("comments", [])
                ]
                await emitter.emit_status("History retrieved", True)
                return json.dumps(comments, indent=2)
            elif action == "add":
                await emitter.emit_status(f"Posting comment to {issue_id}", False)
                res = client._request(
                    "POST", f"issue/{issue_id}/comment", data={"body": body}
                )
                await emitter.emit_status("Comment posted", True)
                return f"Comment added successfully. Comment ID: {res.get('id')}"
            else:
                return "Invalid action. Use 'get_all' or 'add'."
        except Exception as e:
            await emitter.emit_status(f"Failed to manage comments: {e}", True, True)
            return f"Error: {e}"

    async def transition_jira_issue(
        self,
        issue_id: str,
        target_status: str,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Transition a Jira issue to a new status (e.g. 'In Progress', 'Done', 'To Do').
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(f"Transitioning {issue_id} to {target_status}", False)
        try:
            client = self._get_client()

            transitions_data = client._request("GET", f"issue/{issue_id}/transitions")
            available = [
                {"id": t["id"], "name": t["name"], "to_status": t["to"]["name"]}
                for t in transitions_data.get("transitions", [])
            ]

            transition_to_use = None
            for t in available:
                if (
                    target_status.lower() in t["to_status"].lower()
                    or target_status.lower() in t["name"].lower()
                ):
                    transition_to_use = t["id"]
                    break

            if not transition_to_use:
                available_names = [t["to_status"] for t in available]
                raise JiraApiError(
                    f"Cannot transition to '{target_status}'. Available: {available_names}"
                )

            client._request(
                "POST",
                f"issue/{issue_id}/transitions",
                data={"transition": {"id": transition_to_use}},
            )
            await emitter.emit_status(f"{issue_id} transitioned successfully", True)

            return json.dumps(
                {
                    "issue_key": issue_id,
                    "message": f"Successfully moved to {target_status}",
                    "link": f"{client.base_url}/browse/{issue_id}",
                },
                indent=2,
            )
        except Exception as e:
            await emitter.emit_status(f"Failed to transition issue: {e}", True, True)
            return f"Error: {e}"

    async def update_jira_issue(
        self,
        issue_key: str,
        summary: str = None,
        description: str = None,
        acceptance_criteria: str = None,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Update an existing Jira issue's fields.
        Use this to change the Title (summary), Description, or Acceptance Criteria of an existing ticket.
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(f"Updating fields for {issue_key}...", False)
        try:
            client = self._get_client()

            fields = {}
            if summary:
                fields["summary"] = summary
            if description:
                fields["description"] = description
            if acceptance_criteria:
                fields["customfield_10199"] = acceptance_criteria

            if not fields:
                return "No fields provided to update."

            client._request("PUT", f"issue/{issue_key}", data={"fields": fields})

            await emitter.emit_status(f"Successfully updated {issue_key}", True)
            return json.dumps(
                {
                    "status": "success",
                    "issue_key": issue_key,
                    "updated_fields": list(fields.keys()),
                    "link": f"{client.base_url}/browse/{issue_key}",
                },
                indent=2,
            )

        except Exception as e:
            await emitter.emit_status(f"Update failed: {e}", True, True)
            return f"Error updating {issue_key}: {e}"

    async def create_jira_issue(
        self,
        project_key: str,
        summary: str,
        requesting_org: str,
        benefitting_org: str,
        delivery_area: str,
        user_story: str,
        current_state: str,
        desired_state: str,
        acceptance_criteria: str,
        issue_type: str = "Story",
        additional_context: str = "",
        priority: str = "",
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Create a new Jira issue. Before calling this tool, you MUST have ALL required fields.
        If the user has not provided some fields, you MUST ask them. If the user asks you to fill in
        or generate content, use your knowledge to write professional content for the missing fields.
        Always ask for customfield_10188 (Delivery Area), customfield_12007 (Benefitting Org),
        and customfield_10077 (Requesting Org) if they are not provided.

        Required fields - ask the user if missing:
        - project_key: Jira project key (e.g. 'ITDEV', 'INT')
        - summary: Brief title of the issue
        - requesting_org: e.g. 'IT', 'Engineering', 'Sales', 'Marketing', 'Finance', 'HR', 'Legal', 'Security'
        - benefitting_org: e.g. 'IT', 'Engineering', 'Sales', 'Marketing', 'Finance', 'HR', 'Legal', 'Security'
        - delivery_area: e.g. 'ElasticGPT', 'CoE', 'Platform', 'INT 2'
        - user_story: Format as 'As a [role], I want [goal], so that [benefit]'
        - current_state: Describe the current situation or problem
        - desired_state: Describe what the end result should look like
        - acceptance_criteria: List of conditions that must be met. Use '* ' prefix for each bullet point.

        Optional fields:
        - issue_type: Story, Task, or Bug (default: Story)
        - additional_context: Any extra information, links, or references. This goes into the main Description box.
        - priority: High, Medium, or Low
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(f"Creating {issue_type} in {project_key}", False)
        try:
            client = self._get_client()

            issue_data = {
                "fields": {
                    "project": {"key": project_key},
                    "summary": summary,
                    "issuetype": {"name": issue_type},
                    "customfield_10077": [{"value": requesting_org}],
                    "customfield_12007": [{"value": benefitting_org}],
                    "customfield_10188": {"value": delivery_area},
                    "customfield_11863": user_story,
                    "customfield_10197": current_state,
                    "customfield_10198": desired_state,
                    "customfield_10199": acceptance_criteria,
                }
            }

            if additional_context:
                issue_data["fields"]["description"] = additional_context

            if priority:
                issue_data["fields"]["priority"] = {"name": priority}

            result = client._request("POST", "issue", data=issue_data, is_agile=False)

            await emitter.emit_status(f"Created {result.get('key')}", True)

            return json.dumps(
                {
                    "key": result.get("key"),
                    "link": f"{client.base_url}/browse/{result.get('key')}",
                    "summary": summary,
                },
                indent=2,
            )
        except Exception as e:
            await emitter.emit_status(f"Failed to create issue: {e}", True, True)
            return f"Error: {e}"

    async def clone_github_to_jira(
        self,
        project_key: str,
        github_url: str,
        github_title: str,
        github_body: str,
        requesting_org: str = "IT",
        benefitting_org: str = "IT",
        delivery_area: str = "ElasticGPT",
        issue_type: str = "Story",
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Use this tool specifically to clone a GitHub issue directly to Jira.
        You MUST first fetch the issue from GitHub using the GitHub tool, then pass its URL, Title, and Body into this tool.
        CRITICAL: Do not pass the raw, giant GitHub body into this tool. Read the raw body, SUMMARIZE it into
        a clear, concise 2-3 paragraph explanation, and pass your SUMMARY into the 'github_body' argument.
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(f"Cloning GitHub issue into {project_key}...", False)

        # Truncate body to protect Jira's character limits on description fields
        safe_body = (
            github_body[:30000] + "\n...[Truncated]"
            if len(github_body) > 30000
            else github_body
        )
        formatted_description = f"h2. Cloned from GitHub\n*Original Issue:* {github_url}\n\n*GitHub Body:*\n{safe_body}"

        return await self.create_jira_issue(
            project_key=project_key,
            summary=f"[GitHub] {github_title}",
            requesting_org=requesting_org,
            benefitting_org=benefitting_org,
            delivery_area=delivery_area,
            user_story=f"As a developer, I need to address the cloned GitHub issue: {github_title}",
            current_state="Issue reported in GitHub. Needs to be tracked in Jira.",
            desired_state="GitHub issue is resolved and synced.",
            acceptance_criteria="* Code committed and merged.\n* GitHub issue closed.",
            issue_type=issue_type,
            additional_context=formatted_description,
            __event_emitter__=__event_emitter__,
        )

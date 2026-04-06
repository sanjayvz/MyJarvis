"""
title: Scrum Master Analytics (God-Tier)
description: Advanced Agile metrics tailored to Elastic's Jira schema. Analyzes sprints, identifies scope creep, finds un-groomed backlog tickets, and maps developer workload.
version: 2.2.0
license: MIT
"""

import json
import requests
from datetime import datetime, timedelta
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

    def _request(self, method: str, path: str, params=None, data=None, is_agile=False):
        # 🚀 FIX: Forced to api/3 for the new Atlassian search rules
        api_type = "agile/1.0" if is_agile else "api/3"
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

        if response.status_code in [201, 204] or not response.text:
            return {"status": "success"}

        return response.json()


class Tools:
    def __init__(self):
        self.valves = self.Valves()

    class Valves(BaseModel):
        base_url: str = Field(
            "", description="Jira base URL (e.g. https://elasticco.atlassian.net)"
        )
        username: str = Field("", description="Jira username or email")
        password: str = Field("", description="Jira API token")
        pat: str = Field("", description="Personal Access Token (PAT)")

    def _get_client(self) -> JiraClient:
        if not self.valves.base_url:
            raise JiraApiError("Jira base URL not configured.")
        return JiraClient(
            self.valves.username,
            self.valves.password,
            self.valves.base_url,
            self.valves.pat,
        )

    # --- CONSTANTS DERIVED FROM YOUR PAYLOAD ---
    FIELD_STORY_POINTS = "customfield_10016"
    FIELD_ACCEPTANCE_CRITERIA = "customfield_10199"
    FIELD_USER_STORY = "customfield_11863"
    FIELD_PEER_REVIEWER = "customfield_10071"
    FIELD_QA_ASSIGNEE = "customfield_10078"
    FIELD_COMPLEXITY = "customfield_10079"
    FIELD_RAG_STATUS = "customfield_12338"

    async def analyze_active_sprint(
        self, board_id: int, __event_emitter__: Callable[[dict], Awaitable[None]] = None
    ) -> str:
        """
        The Ultimate Standup & Retro Prep Tool.
        Calculates velocity, scope creep, stale tickets, missing acceptance criteria, and missing reviewers.
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(
            f"Running Deep Scrum Analysis for Board {board_id}...", False
        )

        try:
            client = self._get_client()

            # Fetch Active Sprint
            sprints = client._request(
                "GET",
                f"board/{board_id}/sprint",
                params={"state": "active"},
                is_agile=True,
            )
            if not sprints.get("values"):
                return "No active sprint found for this board."

            sprint = sprints["values"][0]
            sprint_start = sprint.get("startDate", "")

            # Fetch all issues in sprint
            issues_data = client._request(
                "GET",
                f"sprint/{sprint['id']}/issue",
                params={"maxResults": 100},
                is_agile=True,
            )
            issues = issues_data.get("issues", [])

            # Metrics Initialization
            total_points = 0.0
            completed_points = 0.0

            alerts = {
                "missing_story_points": [],
                "missing_acceptance_criteria": [],
                "stuck_in_review_missing_reviewer": [],
                "stale_no_updates_48h": [],
                "scope_creep_added_mid_sprint": [],
            }

            developer_workload = {}
            two_days_ago = datetime.now() - timedelta(days=2)

            for issue in issues:
                key = issue.get("key")
                fields = issue.get("fields", {})

                status = fields.get("status", {}).get("name", "Unknown")
                assignee = fields.get("assignee", {}).get("displayName", "Unassigned")
                updated_str = fields.get("updated", "")
                created_str = fields.get("created", "")
                summary = fields.get("summary", "No Summary Provided")

                # Custom Fields mapped to your schema
                points = fields.get(self.FIELD_STORY_POINTS)
                ac = fields.get(self.FIELD_ACCEPTANCE_CRITERIA)
                reviewer = fields.get(self.FIELD_PEER_REVIEWER)

                # --- WORKLOAD & VELOCITY MATH ---
                pts = float(points) if points is not None else 0.0
                total_points += pts

                if assignee not in developer_workload:
                    developer_workload[assignee] = {
                        "assigned_points": 0.0,
                        "completed_points": 0.0,
                        "ticket_count": 0,
                    }

                developer_workload[assignee]["assigned_points"] += pts
                developer_workload[assignee]["ticket_count"] += 1

                if status.lower() in ["done", "closed", "resolved"]:
                    completed_points += pts
                    developer_workload[assignee]["completed_points"] += pts

                # --- BACKLOG GROOMING ALERTS ---
                if points is None and status.lower() not in [
                    "done",
                    "closed",
                    "resolved",
                ]:
                    alerts["missing_story_points"].append(
                        {"key": key, "summary": summary, "assignee": assignee}
                    )

                if not ac and status.lower() not in ["done", "closed", "resolved"]:
                    alerts["missing_acceptance_criteria"].append(
                        {"key": key, "summary": summary, "assignee": assignee}
                    )

                # --- BOTTLENECK ALERTS ---
                if "review" in status.lower() and not reviewer:
                    alerts["stuck_in_review_missing_reviewer"].append(
                        {"key": key, "summary": summary, "assignee": assignee}
                    )

                # --- STALE TICKET ALERTS ---
                if updated_str and status.lower() not in ["done", "closed", "resolved"]:
                    try:
                        updated_date = datetime.strptime(
                            updated_str.split("T")[0], "%Y-%m-%d"
                        )
                        if updated_date < two_days_ago:
                            alerts["stale_no_updates_48h"].append(
                                {
                                    "key": key,
                                    "summary": summary,
                                    "assignee": assignee,
                                    "days_stale": (datetime.now() - updated_date).days,
                                }
                            )
                    except:
                        pass

                # --- SCOPE CREEP ALERTS ---
                if created_str and sprint_start:
                    try:
                        created_date = datetime.strptime(
                            created_str.split("T")[0], "%Y-%m-%d"
                        )
                        sprint_start_date = datetime.strptime(
                            sprint_start.split("T")[0], "%Y-%m-%d"
                        )
                        if created_date > sprint_start_date:
                            alerts["scope_creep_added_mid_sprint"].append(
                                {
                                    "key": key,
                                    "summary": summary,
                                    "points": pts,
                                    "assignee": assignee,
                                }
                            )
                    except:
                        pass

            completion_rate = (
                round((completed_points / total_points * 100), 1)
                if total_points > 0
                else 0
            )

            report = {
                "sprint_name": sprint["name"],
                "velocity_health": {
                    "total_story_points": total_points,
                    "completed_points": completed_points,
                    "completion_percentage": f"{completion_rate}%",
                },
                "developer_workload": developer_workload,
                "scrum_alerts": alerts,
            }

            await emitter.emit_status(
                f"Analyzed {len(issues)} tickets. Sprint is {completion_rate}% complete.",
                True,
            )
            return json.dumps(report, indent=2)

        except Exception as e:
            await emitter.emit_status(f"Failed to run Scrum Analysis: {e}", True, True)
            return f"Error: {e}"

    async def identify_backlog_gaps(
        self,
        project_key: str,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Pre-Sprint Grooming Tool.
        Scans the open backlog for a project and flags tickets missing Acceptance Criteria, User Stories, or Story Points.
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(f"Scanning backlog for {project_key}...", False)

        try:
            client = self._get_client()
            # JQL: Not in an active sprint, not done
            jql = f'project = "{project_key}" AND sprint in EMPTY AND statusCategory != Done ORDER BY rank ASC'

            # 🚀 FIX: Using search/jql for the v3 API
            res = client._request(
                "GET",
                "search/jql",
                params={
                    "jql": jql,
                    "maxResults": 30,
                    "fields": f"summary,status,assignee,{self.FIELD_STORY_POINTS},{self.FIELD_ACCEPTANCE_CRITERIA},{self.FIELD_USER_STORY}",
                },
            )

            issues = res.get("issues", [])
            ungroomed_tickets = []

            for issue in issues:
                fields = issue.get("fields", {})
                key = issue.get("key")

                missing = []
                if not fields.get(self.FIELD_STORY_POINTS):
                    missing.append("Story Points")
                if not fields.get(self.FIELD_ACCEPTANCE_CRITERIA):
                    missing.append("Acceptance Criteria")
                if not fields.get(self.FIELD_USER_STORY):
                    missing.append("User Story")

                if missing:
                    ungroomed_tickets.append(
                        {
                            "key": key,
                            "summary": fields.get("summary"),
                            "assignee": (
                                fields.get("assignee", {}).get(
                                    "displayName", "Unassigned"
                                )
                                if fields.get("assignee")
                                else "Unassigned"
                            ),
                            "missing_fields": missing,
                        }
                    )

            report = {
                "scanned_tickets": len(issues),
                "ungroomed_count": len(ungroomed_tickets),
                "requires_grooming": ungroomed_tickets,
            }

            await emitter.emit_status(
                f"Found {len(ungroomed_tickets)} tickets needing grooming.", True
            )
            return json.dumps(report, indent=2)

        except Exception as e:
            await emitter.emit_status(f"Failed to scan backlog: {e}", True, True)
            return f"Error: {e}"

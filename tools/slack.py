"""
title: SLACK
description: God-Mode Slack Agent. Send/read messages, resolve users, manage reactions, check mentions, and read bookmarks.
version: 3.4.0
license: MIT
"""

import json
import re
import requests
from typing import Any, Awaitable, Callable, Dict, Optional, List
from pydantic import BaseModel, Field


class EventEmitter:
    def __init__(self, event_emitter: Callable[[dict], Awaitable[None]]):
        self.event_emitter = event_emitter

    async def emit_status(self, description: str, done: bool, error: bool = False):
        if self.event_emitter:
            await self.event_emitter(
                {
                    "data": {
                        "description": f"{'❌' if done and error else '✅' if done else '💬'} {description}",
                        "status": "complete" if done else "in_progress",
                        "done": done,
                    },
                    "type": "status",
                }
            )


class Tools:
    def __init__(self):
        self.valves = self.Valves()

    class Valves(BaseModel):
        slack_token: str = Field("", description="Slack Bot OAuth Token (xoxb-)")

    def _get_headers(self):
        return {
            "Authorization": f"Bearer {self.valves.slack_token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def _resolve_target(self, target: str) -> str:
        """Automatically converts #channels, @users, or emails into Slack IDs."""
        target = target.strip()

        # If it's already a raw ID
        if target.startswith(("C", "U", "G")) and len(target) > 6 and not "@" in target:
            return target

        headers = {
            "Authorization": f"Bearer {self.valves.slack_token}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        # Handle Email
        if "@" in target and "." in target:
            res = requests.get(
                "https://slack.com/api/users.lookupByEmail",
                headers=headers,
                params={"email": target},
            ).json()
            if res.get("ok"):
                return res["user"]["id"]
            else:
                raise ValueError(
                    f"Slack API error looking up email: {res.get('error')}"
                )

        # Handle Channel Name
        if target.startswith("#"):
            clean_name = target.replace("#", "").lower()
            cursor = ""
            while True:
                params = {
                    "exclude_archived": "true",
                    "types": "public_channel,private_channel",
                    "limit": 200,
                }
                if cursor:
                    params["cursor"] = cursor

                res = requests.get(
                    "https://slack.com/api/conversations.list",
                    headers=headers,
                    params=params,
                ).json()

                if not res.get("ok"):
                    raise ValueError(
                        f"Slack API refused to list channels. Error code: '{res.get('error')}'. (Missing channels:read scope?)"
                    )

                for c in res.get("channels", []):
                    if c.get("name", "").lower() == clean_name:
                        return c.get("id")

                cursor = res.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break

            raise ValueError(
                f"Channel '{target}' was not found in the directory. If it is private, type '/invite @jarvis' inside the channel first."
            )

        # Handle @Username
        if target.startswith("@"):
            clean_user = target.replace("@", "").lower()
            res = requests.get(
                "https://slack.com/api/users.list", headers=headers
            ).json()

            if not res.get("ok"):
                raise ValueError(
                    f"Slack API refused to list users. Error code: '{res.get('error')}' (Missing users:read scope?)"
                )

            for u in res.get("members", []):
                if (
                    u.get("name", "").lower() == clean_user
                    or u.get("real_name", "").lower() == clean_user
                ):
                    return u.get("id")
            raise ValueError(f"User '{target}' was not found.")

        return target

    def _is_placeholder(self, text: str) -> bool:
        """Detect if the AI passed a placeholder variable instead of real content."""
        stripped = text.strip()
        if re.match(r"^\{+[^}]+\}+$", stripped) or not stripped:
            return True
        return False

    async def send_message(
        self,
        target: str,
        message: str,
        thread_ts: str = "",
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Send a message to a channel, user, or email.
        RULES: 'target' can be #channel, @username, email, or Slack ID.
        """
        emitter = EventEmitter(__event_emitter__)

        if self._is_placeholder(message):
            await emitter.emit_status(
                "Message rejected — placeholder detected", True, True
            )
            return "Error: message contains a placeholder instead of real content."

        await emitter.emit_status(f"Resolving target '{target}'...", False)

        try:
            target_id = self._resolve_target(target)
        except Exception as e:
            await emitter.emit_status(f"Resolution failed: {e}", True, True)
            return str(e)

        # Open DM tunnel if target is a User ID
        if target_id.startswith("U"):
            res = requests.post(
                "https://slack.com/api/conversations.open",
                headers={
                    "Authorization": f"Bearer {self.valves.slack_token}",
                    "Content-Type": "application/json",
                },
                json={"users": target_id},
            ).json()
            if res.get("ok"):
                target_id = res["channel"]["id"]
            else:
                return f"Error opening DM: {res.get('error')} (Missing im:write scope?)"

        payload = {"channel": target_id, "text": message}
        if thread_ts:
            payload["thread_ts"] = thread_ts

        await emitter.emit_status(f"Sending message to {target_id}...", False)
        try:
            res = requests.post(
                "https://slack.com/api/chat.postMessage",
                headers=self._get_headers(),
                json=payload,
            ).json()
            if not res.get("ok"):
                error_msg = res.get("error")
                await emitter.emit_status(f"Failed: {error_msg}", True, True)
                return f"Error sending message: {error_msg}"

            await emitter.emit_status("Message delivered successfully.", True)
            return f"Success! Message sent to {target}. Timestamp: {res.get('ts')}"
        except Exception as e:
            return f"Error: {e}"

    async def read_messages(
        self,
        target: str,
        limit: int = 15,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Read the most recent messages from a channel, DM, or user.
        RULES: 'target' can be #channel, @username, email, or ID.
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(f"Fetching history from {target}...", False)

        try:
            target_id = self._resolve_target(target)
        except Exception as e:
            await emitter.emit_status("Resolution failed", True, True)
            return str(e)

        try:
            headers = {
                "Authorization": f"Bearer {self.valves.slack_token}",
                "Content-Type": "application/x-www-form-urlencoded",
            }
            res = requests.get(
                "https://slack.com/api/conversations.history",
                headers=headers,
                params={"channel": target_id, "limit": limit},
            ).json()

            if not res.get("ok"):
                return f"Read Error: {res.get('error')}"

            messages = []
            for msg in res.get("messages", []):
                if msg.get("subtype"):
                    continue
                messages.append(
                    f"[{msg.get('ts')}] {msg.get('user', 'Unknown')}: {msg.get('text', '')}"
                )

            await emitter.emit_status(f"Read {len(messages)} messages.", True)
            return "\n".join(messages) if messages else "No messages found."
        except Exception as e:
            return f"Error: {e}"

    async def check_mentions(
        self,
        limit: int = 5,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """Check if anyone has mentioned or pinged the AI Agent recently."""
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status("Checking for recent @mentions...", False)

        try:
            headers = {
                "Authorization": f"Bearer {self.valves.slack_token}",
                "Content-Type": "application/x-www-form-urlencoded",
            }
            res = requests.get(
                "https://slack.com/api/app.mentions",
                headers=headers,
                params={"limit": limit},
            ).json()

            if not res.get("ok"):
                return f"Mention Error: {res.get('error')}"

            mentions = []
            for msg in res.get("messages", []):
                mentions.append(
                    f"Channel {msg.get('channel')} | User {msg.get('user')}: {msg.get('text')} (TS: {msg.get('ts')})"
                )

            await emitter.emit_status(f"Found {len(mentions)} mentions.", True)
            return (
                "\n".join(mentions)
                if mentions
                else "Nobody has mentioned you recently."
            )
        except Exception as e:
            return f"Error: {e}"

    async def add_reaction(
        self,
        target: str,
        timestamp: str,
        emoji_name: str,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """Add an emoji reaction to a specific message."""
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(f"Adding :{emoji_name}: reaction...", False)

        try:
            target_id = self._resolve_target(target)
        except Exception as e:
            return str(e)

        payload = {
            "channel": target_id,
            "timestamp": timestamp,
            "name": emoji_name.replace(":", ""),
        }

        try:
            res = requests.post(
                "https://slack.com/api/reactions.add",
                headers=self._get_headers(),
                json=payload,
            ).json()
            if not res.get("ok"):
                return f"Reaction Error: {res.get('error')}"
            await emitter.emit_status("Reaction added.", True)
            return f"Successfully added {emoji_name} reaction."
        except Exception as e:
            return f"Error: {e}"

    async def search_directory(
        self,
        query: str,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """Search the Slack workspace for a user by name, email, or role."""
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(f"Searching directory for '{query}'...", False)

        try:
            headers = {
                "Authorization": f"Bearer {self.valves.slack_token}",
                "Content-Type": "application/x-www-form-urlencoded",
            }
            res = requests.get(
                "https://slack.com/api/users.list", headers=headers
            ).json()

            if not res.get("ok"):
                return f"Search Error: {res.get('error')}"

            results = []
            query_lower = query.lower()
            for u in res.get("members", []):
                profile = u.get("profile", {})
                search_string = f"{u.get('name')} {profile.get('real_name')} {profile.get('email')} {profile.get('title')}".lower()

                if query_lower in search_string and not u.get("deleted"):
                    results.append(
                        f"Name: {profile.get('real_name')} | Title: {profile.get('title')} | Email: {profile.get('email')} | ID: {u.get('id')}"
                    )

            await emitter.emit_status(f"Found {len(results)} users.", True)
            return (
                "\n".join(results) if results else f"No users found matching '{query}'."
            )
        except Exception as e:
            return f"Error: {e}"

    async def get_bookmarks(
        self,
        target: str,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """Read all the pinned Bookmarks at the top of a channel."""
        emitter = EventEmitter(__event_emitter__)
        await emitter.emit_status(f"Fetching bookmarks for {target}...", False)

        try:
            target_id = self._resolve_target(target)
        except Exception as e:
            return str(e)

        try:
            headers = {
                "Authorization": f"Bearer {self.valves.slack_token}",
                "Content-Type": "application/x-www-form-urlencoded",
            }
            res = requests.get(
                "https://slack.com/api/bookmarks.list",
                headers=headers,
                params={"channel_id": target_id},
            ).json()

            if not res.get("ok"):
                return f"Bookmark Error: {res.get('error')}"

            bookmarks = []
            for b in res.get("bookmarks", []):
                bookmarks.append(
                    f"Title: {b.get('title')} | Link: {b.get('link')} | Type: {b.get('type')}"
                )

            await emitter.emit_status(f"Found {len(bookmarks)} bookmarks.", True)
            return (
                "\n".join(bookmarks)
                if bookmarks
                else "No bookmarks found in this channel."
            )
        except Exception as e:
            return f"Error: {e}"

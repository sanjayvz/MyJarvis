"""
title: Mac Jarvis Controller
description: Ultimate Mac Assistant: Control apps, system settings, read/send Apple Mail, manage files, read clipboard, execute AppleScript, and read/summarize active browser tab content.
version: 1.1.0
license: MIT
"""

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Awaitable, Callable, Optional
from pydantic import BaseModel


class EventEmitter:
    def __init__(self, event_emitter: Callable[[dict], Awaitable[None]]):
        self.event_emitter = event_emitter

    async def status(self, description: str, done: bool, error: bool = False):
        if self.event_emitter:
            await self.event_emitter(
                {
                    "type": "status",
                    "data": {
                        "description": f"{'❌' if error and done else '✅' if done else '⚙️'} {description}",
                        "status": "complete" if done else "in_progress",
                        "done": done,
                    },
                }
            )


class Tools:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()

    def _run_applescript(self, script: str) -> str:
        """Helper to run AppleScript and return the output."""
        try:
            result = subprocess.run(
                ["osascript", "-e", script], capture_output=True, text=True, check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"AppleScript Error: {e.stderr.strip()}")

    # ── 1. System Toggles ─────────────────────────────────────────

    async def control_mac_system(
        self,
        action: str,
        value: Optional[int] = None,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Control Mac system settings.
        action: Must be one of: "dark_mode_on", "dark_mode_off", "set_volume", "mute", "unmute", "sleep".
        value: The volume level (0-100) if action is "set_volume".
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.status(f"Executing system action: {action}", False)

        try:
            if action == "dark_mode_on":
                self._run_applescript(
                    'tell application "System Events" to tell appearance preferences to set dark mode to true'
                )
                res = "Dark mode enabled."
            elif action == "dark_mode_off":
                self._run_applescript(
                    'tell application "System Events" to tell appearance preferences to set dark mode to false'
                )
                res = "Dark mode disabled."
            elif action == "set_volume" and value is not None:
                self._run_applescript(f"set volume output volume {value}")
                res = f"Volume set to {value}%."
            elif action == "mute":
                self._run_applescript("set volume with output muted")
                res = "System muted."
            elif action == "unmute":
                self._run_applescript("set volume without output muted")
                res = "System unmuted."
            elif action == "sleep":
                self._run_applescript('tell application "Finder" to sleep')
                res = "Mac is going to sleep."
            else:
                return f"Invalid action: {action}"

            await emitter.status(res, True)
            return res
        except Exception as e:
            await emitter.status(f"Failed: {e}", True, True)
            return str(e)

    # ── 2. App & Window Management ────────────────────────────────

    async def manage_apps(
        self,
        action: str,
        app_name: str,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Open or Quit macOS applications.
        action: "open" or "quit"
        app_name: The name of the app (e.g., "Safari", "Spotify", "Visual Studio Code")
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.status(f"{action.title()}ing {app_name}...", False)

        try:
            if action == "open":
                self._run_applescript(f'tell application "{app_name}" to activate')
                res = f"Opened {app_name}."
            elif action == "quit":
                self._run_applescript(f'tell application "{app_name}" to quit')
                res = f"Quit {app_name}."
            else:
                return "Invalid action. Use 'open' or 'quit'."

            await emitter.status(res, True)
            return res
        except Exception as e:
            await emitter.status(f"Failed: {e}", True, True)
            return str(e)

    # ── 3. Media Controls ─────────────────────────────────────────

    async def control_media(
        self,
        command: str,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Control media playback via the standard Mac media keys.
        command: "playpause", "next", or "previous"
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.status(f"Media command: {command}", False)

        key_codes = {"playpause": 16, "next": 19, "previous": 20}

        if command not in key_codes:
            return "Invalid command. Use playpause, next, or previous."

        try:
            script = (
                f'tell application "System Events" to key code {key_codes[command]}'
            )
            self._run_applescript(script)
            await emitter.status(f"Executed {command}", True)
            return f"Successfully pressed {command} key."
        except Exception as e:
            await emitter.status(f"Failed: {e}", True, True)
            return str(e)

    # ── 4a. Browser URL Reader ────────────────────────────────────

    async def get_active_browser_url(
        self,
        browser: str = "Chrome",
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Gets ONLY the URL of the currently active tab in Safari or Google Chrome.
        Use this when you only need the URL itself (e.g. to share a link).
        If you need the page content or want to summarize what the user is looking at,
        use get_active_tab_content or summarize_active_tab instead.
        browser: "Chrome" or "Safari"
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.status(f"Fetching URL from {browser}...", False)

        try:
            if browser.lower() == "safari":
                script = 'tell application "Safari" to return URL of front document'
            else:
                script = 'tell application "Google Chrome" to return URL of active tab of front window'

            url = self._run_applescript(script)
            await emitter.status("URL Fetched", True)
            return f"The user is currently looking at: {url}"
        except Exception as e:
            await emitter.status(f"Failed to get URL: {e}", True, True)
            return f"Error (Is the browser open?): {e}"

    # ── 4b. Active Tab Content Reader ────────────────────────────

    async def get_active_tab_content(
        self,
        browser: str = "Chrome",
        max_chars: int = 8000,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Reads the FULL visible text content of the active browser tab by executing
        JavaScript directly inside the page. Use this when the user wants to summarize,
        analyze, or ask questions about the page they are currently viewing.
        Always prefer this over get_active_browser_url when page content is needed.

        browser: "Chrome" or "Safari"
        max_chars: Maximum characters to return (default 8000 to stay within context).
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.status(f"Reading page content from {browser}...", False)

        try:
            # Step 1: Get the URL for context
            if browser.lower() == "safari":
                url_script = 'tell application "Safari" to return URL of front document'
            else:
                url_script = 'tell application "Google Chrome" to return URL of active tab of front window'

            url = self._run_applescript(url_script)

            # Step 2: Extract visible page text via JavaScript
            js = "document.body.innerText"

            if browser.lower() == "safari":
                content_script = f'tell application "Safari" to do JavaScript "{js}" in front document'
            else:
                content_script = f'tell application "Google Chrome" to execute front window\'s active tab javascript "{js}"'

            raw_text = self._run_applescript(content_script)

            if not raw_text or raw_text.strip() == "":
                await emitter.status("Page returned no readable text.", True, True)
                return (
                    f"URL: {url}\n\nThe page at this URL has no readable text content "
                    f"(it may be a web app, PDF viewer, or login-gated page)."
                )

            # Step 3: Clean up excessive whitespace and truncate
            cleaned = re.sub(r"\n{3,}", "\n\n", raw_text).strip()

            truncated = False
            if len(cleaned) > max_chars:
                cleaned = cleaned[:max_chars]
                truncated = True

            await emitter.status("Page content read successfully.", True)

            result = f"URL: {url}\n\n--- PAGE CONTENT ---\n{cleaned}"
            if truncated:
                result += f"\n\n[CONTENT TRUNCATED AT {max_chars} CHARS]"

            return result

        except Exception as e:
            await emitter.status(f"Failed to read page content: {e}", True, True)
            return (
                f"Error reading page content: {e}\n"
                "Tip: Make sure the browser is open, not on a chrome:// or file:// URL, "
                "and that Accessibility/Automation permissions are granted for the browser in "
                "System Settings → Privacy & Security → Automation."
            )

    # ── 4c. Summarize Active Tab ──────────────────────────────────

    async def summarize_active_tab(
        self,
        browser: str = "Chrome",
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Convenience shortcut: reads the active tab's full content and returns it
        ready for the LLM to summarize. Use when the user says things like:
        'summarize what I'm looking at', 'what is on my screen', 'explain this page',
        'what does this page say', 'can you read this for me'.

        browser: "Chrome" or "Safari"
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.status("Grabbing page for summary...", False)

        content = await self.get_active_tab_content(
            browser=browser,
            max_chars=8000,
            __event_emitter__=__event_emitter__,
        )

        return (
            f"{content}\n\n"
            "---\n"
            "The above is the full visible text of the user's active tab. "
            "Please provide a clear, concise summary of this page."
        )

    # ── 5. Communication (iMessage) ───────────────────────────────

    async def send_imessage(
        self,
        phone_number_or_email: str,
        message: str,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Sends an iMessage/SMS via the Mac Messages app.
        phone_number_or_email: The recipient's contact info.
        message: The text to send.
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.status("Sending iMessage...", False)

        script = f"""
        tell application "Messages"
            set targetService to 1st service whose service type = iMessage
            set targetBuddy to buddy "{phone_number_or_email}" of targetService
            send "{message}" to targetBuddy
        end tell
        """
        try:
            self._run_applescript(script)
            await emitter.status("Message sent.", True)
            return f"Successfully sent '{message}' to {phone_number_or_email}."
        except Exception as e:
            await emitter.status(f"Failed: {e}", True, True)
            return f"Failed to send iMessage. Note: Contact must be registered in Messages app. Error: {e}"

    # ── 6. Desktop Organizer ──────────────────────────────────────

    async def organize_desktop(
        self,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Organizes the user's Mac desktop by moving loose files into categorized folders.
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.status("Organizing Desktop...", False)

        desktop_path = Path.home() / "Desktop"
        if not desktop_path.exists():
            return "Error: Could not locate Desktop folder."

        categories = {
            "Images": [".png", ".jpg", ".jpeg", ".gif", ".svg", ".heic"],
            "Documents": [".pdf", ".docx", ".txt", ".csv", ".xlsx", ".pptx"],
            "Installers": [".dmg", ".pkg"],
            "Archives": [".zip", ".tar", ".gz", ".rar"],
            "Code": [".py", ".js", ".html", ".css", ".json", ".sh"],
        }

        moved_files = []
        try:
            for file_path in desktop_path.iterdir():
                if file_path.is_file() and not file_path.name.startswith("."):
                    ext = file_path.suffix.lower()

                    target_folder_name = "Other"
                    for cat, exts in categories.items():
                        if ext in exts:
                            target_folder_name = cat
                            break

                    target_dir = desktop_path / target_folder_name
                    target_dir.mkdir(exist_ok=True)
                    shutil.move(str(file_path), str(target_dir / file_path.name))
                    moved_files.append(file_path.name)

            await emitter.status("Desktop Organized", True)
            return (
                f"Moved {len(moved_files)} files into category folders on the Desktop."
            )
        except Exception as e:
            await emitter.status(f"Failed: {e}", True, True)
            return str(e)

    # ── 7. Local File Reader ──────────────────────────────────────

    async def read_local_file(
        self,
        file_path: str,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Reads the text content of a local file on the Mac.
        file_path: Absolute path to the file (e.g., '~/Desktop/todo.md')
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.status(f"Reading file: {file_path}", False)

        try:
            path = Path(file_path).expanduser()
            if not path.exists():
                await emitter.status("File not found.", True, True)
                return f"Error: Could not find any file at {path}"
            if not path.is_file():
                await emitter.status("Path is a directory.", True, True)
                return f"Error: {path} is a folder, not a file."

            content = path.read_text(errors="ignore")
            max_chars = 15000
            if len(content) > max_chars:
                await emitter.status("File is very large, reading first chunk...", True)
                return (
                    f"File is too large to read entirely. First {max_chars} characters:\n\n"
                    f"{content[:max_chars]}...\n\n[FILE TRUNCATED]"
                )

            await emitter.status("File read successfully.", True)
            return content
        except Exception as e:
            await emitter.status(f"Failed to read file: {e}", True, True)
            return f"Error reading file: {e}"

    # ── 8. Voice TTS ──────────────────────────────────────────────

    async def speak_out_loud(
        self,
        text: str,
        voice: str = "Daniel",
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Speaks text out loud using the Mac's built-in text-to-speech engine.
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.status("Speaking...", False)
        try:
            subprocess.run(["say", "-v", voice, text], check=True)
            await emitter.status("Finished speaking.", True)
            return f"Successfully said: '{text}' out loud."
        except Exception as e:
            await emitter.status("Failed to speak.", True, True)
            return f"Error: {e}"

    # ── 9. Clipboard Manager ──────────────────────────────────────

    async def manage_clipboard(
        self,
        action: str,
        text: str = "",
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Reads from or writes to the Mac clipboard.
        action: 'read' or 'write'
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.status(f"{action.title()}ing clipboard...", False)

        try:
            if action == "write":
                process = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
                process.communicate(input=text.encode("utf-8"))
                await emitter.status("Clipboard updated.", True)
                return "Successfully copied text to the user's clipboard."
            elif action == "read":
                result = subprocess.run(["pbpaste"], capture_output=True, text=True)
                content = result.stdout
                await emitter.status("Clipboard read.", True)
                return f"The current clipboard contains:\n\n{content}"
            else:
                return "Invalid action. Use 'read' or 'write'."
        except Exception as e:
            await emitter.status("Clipboard error.", True, True)
            return f"Error: {e}"

    # ── 10. Apple Reminders ───────────────────────────────────────

    async def add_mac_reminder(
        self,
        task_name: str,
        list_name: str = "Reminders",
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Adds a new task to the Mac's native Reminders app.
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.status(f"Adding '{task_name}' to Reminders...", False)

        script = f"""
        tell application "Reminders"
            try
                set targetList to list "{list_name}"
                make new reminder at end of targetList with properties {{name:"{task_name}"}}
                return "Success"
            on error
                return "List not found"
            end try
        end tell
        """
        try:
            result = self._run_applescript(script)
            if "List not found" in result:
                await emitter.status("List not found.", True, True)
                return f"Could not find a Reminders list named '{list_name}'."

            await emitter.status("Reminder added.", True)
            return f"Successfully added '{task_name}' to the {list_name} list."
        except Exception as e:
            await emitter.status("Failed to add reminder.", True, True)
            return f"Error: {e}"

    # ── 11. System Diagnostics ────────────────────────────────────

    async def check_system_status(
        self,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Gets current system diagnostics: Battery percentage and current IP address.
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.status("Running diagnostics...", False)

        try:
            batt_res = subprocess.run(
                ["pmset", "-g", "batt"], capture_output=True, text=True
            )
            batt_info = (
                batt_res.stdout.split("\n")[1]
                if len(batt_res.stdout.split("\n")) > 1
                else "Unknown"
            )

            ip_res = subprocess.run(
                ["ipconfig", "getifaddr", "en0"], capture_output=True, text=True
            )
            ip_info = ip_res.stdout.strip() or "Disconnected"

            report = f"Battery Status: {batt_info.strip()}\nLocal IP Address: {ip_info}"
            await emitter.status("Diagnostics complete.", True)
            return report
        except Exception as e:
            await emitter.status("Diagnostics failed.", True, True)
            return f"Error getting system info: {e}"

    # ── 12. Screen Capture ────────────────────────────────────────

    async def take_screenshot(
        self,
        filename: str = "jarvis_screenshot.png",
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Takes a screenshot of the main display and saves it to the Desktop.
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.status("Taking screenshot...", False)

        try:
            desktop_path = Path.home() / "Desktop" / filename
            subprocess.run(["screencapture", "-x", str(desktop_path)], check=True)
            await emitter.status("Screenshot saved.", True)
            return f"Screenshot successfully taken and saved to {desktop_path}"
        except Exception as e:
            await emitter.status("Screenshot failed.", True, True)
            return f"Error taking screenshot: {e}"

    # ── 13. Apple Mail Sender ─────────────────────────────────────

    async def send_apple_mail(
        self,
        recipient_email: str,
        subject: str,
        body: str,
        auto_send: bool = False,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Drafts or sends an email using the native macOS Mail app.
        auto_send: If true, sends immediately. If false, leaves the draft open for review.
        """
        emitter = EventEmitter(__event_emitter__)
        action_word = "Sending" if auto_send else "Drafting"
        await emitter.status(f"{action_word} email to {recipient_email}...", False)

        safe_subject = subject.replace('"', '\\"')
        safe_body = body.replace('"', '\\"')
        safe_email = recipient_email.replace('"', '\\"')

        send_command = "send" if auto_send else ""
        visible_prop = "false" if auto_send else "true"
        activate_command = "" if auto_send else "activate"

        script = f"""
        tell application "Mail"
            set newMessage to make new outgoing message with properties {{subject:"{safe_subject}", content:"{safe_body}", visible:{visible_prop}}}
            tell newMessage
                make new to recipient at end of to recipients with properties {{address:"{safe_email}"}}
                {send_command}
            end tell
            {activate_command}
        end tell
        """

        try:
            self._run_applescript(script)
            await emitter.status(f"Email {action_word.lower()} complete.", True)

            if auto_send:
                return f"Successfully sent email to {recipient_email} with subject '{subject}'."
            else:
                return f"Successfully drafted email to {recipient_email}. It is open on the screen waiting for them to click send."

        except Exception as e:
            await emitter.status("Failed to process email.", True, True)
            return f"Error interacting with Apple Mail: {e}"

    # ── 14. Read Selected Apple Mails ─────────────────────────────

    async def read_selected_emails(
        self,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Reads the text of the currently selected (highlighted) emails in the macOS Apple Mail app.
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.status("Reading selected emails from Apple Mail...", False)

        script = """
        tell application "Mail"
            set theSelection to selection
            if (count of theSelection) is 0 then
                return "NO_SELECTION"
            end if

            set output to ""
            repeat with aMessage in theSelection
                set msgSubject to subject of aMessage
                set msgSender to sender of aMessage
                set msgContent to content of aMessage

                set output to output & "From: " & msgSender & return & "Subject: " & msgSubject & return & "Body: " & return & msgContent & return & "---" & return
            end repeat
            return output
        end tell
        """

        try:
            result = self._run_applescript(script)

            if result.strip() == "NO_SELECTION":
                await emitter.status("No emails selected.", True, True)
                return "The user does not have any emails selected in the Mail app. Tell them to click on an email first."

            await emitter.status("Emails read successfully.", True)

            max_chars = 15000
            if len(result) > max_chars:
                return (
                    f"Here is the content of the selected emails:\n\n"
                    f"{result[:max_chars]}...\n\n[TRUNCATED FOR LENGTH]"
                )

            return f"Here is the content of the selected emails:\n\n{result}"

        except Exception as e:
            await emitter.status(f"Failed to read emails: {e}", True, True)
            return f"Error interacting with Apple Mail: {e}"

    # ── 15. Execute Custom AppleScript ────────────────────────────

    async def execute_custom_applescript(
        self,
        script: str,
        __event_emitter__: Callable[[dict], Awaitable[None]] = None,
    ) -> str:
        """
        Executes raw AppleScript for any commands not covered by the other tools.
        """
        emitter = EventEmitter(__event_emitter__)
        await emitter.status("Running custom script...", False)
        try:
            result = self._run_applescript(script)
            await emitter.status("Executed.", True)
            return f"Success. Output: {result}"
        except Exception as e:
            await emitter.status("Failed.", True, True)
            return f"Error: {e}"

# My Jarvis — Setup Guide

## Prerequisites
- Mac with Apple Silicon (M1/M2/M3), 16GB+ RAM recommended
- Ollama installed: https://ollama.com/download
- Open WebUI installed: https://github.com/open-webui/open-webui

---

## Step 1 — Pull the model
Open Terminal and run:
ollama pull gemma4:26b

## Step 2 — Load the custom Modelfile
From inside the repo folder:
ollama create myjarvis -f modelfile/Modelfile.jarvis

## Step 3 — Start Open WebUI
If using Docker:
docker run -d -p 3000:8080 --add-host=host.docker.internal:host-gateway ghcr.io/open-webui/open-webui:main
Then open http://localhost:3000 in your browser.

---

## Step 4 — Connect Open WebUI to Ollama
1. Open WebUI → click your profile icon (top right) → Settings
2. Go to Connections
3. Set Ollama URL to: http://localhost:11434
4. Click Save

If Open WebUI is running in Docker and Ollama is running natively on Mac, use:
http://host.docker.internal:11434

---

## Step 5 — Select your model
- At the top of the chat screen, click the model dropdown
- Select "myjarvis"

---

## Step 6 — Register the tools
1. Open WebUI → Settings → Tools → click the "+" button
2. Paste each tool file one at a time:
   - tools/jira_tool.py
   - tools/confluence_tool.py
   - tools/github_tool.py
   - tools/slack_tool.py
   - tools/scrum_analytics.py
3. Save each one

---

## Step 7 — Add your tokens
1. Copy the .env.example file → rename it to .env
2. Open .env and fill in your real tokens
3. Each tool reads from these environment variables at runtime

---

## Step 8 — Add the system prompt
1. In Open WebUI, select your model from the dropdown
2. Click the pencil/settings icon next to the model name
3. Paste the full contents of skills/system_prompt.md into the System Prompt field
4. Save

---

## You're ready
Type a message in Open WebUI to test. Try:
"Search Jira for my open tickets this sprint"

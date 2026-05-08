# Flow — getting started

Welcome to **Flow** — a personal kanban that turns your meeting notes into tracked tasks with the help of AI.

This guide walks you through the one-time setup. It takes about 5 minutes.

---

## What you need

- A Windows PC (Windows 10 or 11)
- An internet connection (for the first-time setup and for AI features)
- An API token from the internal LLM gateway — instructions below

---

## Step 1 — install Python (skip if you already have it)

Flow runs on Python 3.10 or newer.

1. Open https://www.python.org/downloads/
2. Click the big yellow **Download Python** button.
3. Run the installer.
4. **Important:** on the very first screen, tick **"Add Python to PATH"** before clicking Install Now.

If you already have Python installed and aren't sure of the version, you can skip this step — `setup.bat` (next step) will tell you if it's too old.

---

## Step 2 — set up Flow

1. Extract this zip somewhere stable, e.g. `C:\Users\<your-name>\Flow`. (Avoid temp folders that get auto-cleaned.)
2. Open the extracted folder.
3. **Double-click `setup.bat`.**
4. A black window opens, prints some progress, and ends with **"Setup complete."** Press a key to close it.

You only do this once.

---

## Step 3 — start Flow

**Double-click `start.bat`.**

Two things happen:
- A black window opens — leave it running. **Closing it shuts Flow down.**
- Your default browser opens to http://localhost:8080 with the Flow splash screen.

To stop Flow later, just close the black window.

---

## Step 4 — paste your API token (first launch only)

The first time Flow opens, the **Settings** window pops up automatically and asks for an API token.

1. Click the link in the help box: it opens the internal token generator (`api-dev.llm-incubator.automotive.cloud`).
2. Sign in with your corporate account.
3. Generate a token and copy it.
4. Paste the token into the **API token** field in Flow.
5. Click **Save**.

The "Process Notes" button in the top right is now enabled.

---

## Step 5 (optional) — pick where your cards live

By default Flow stores your cards in `C:\Users\<your-name>\Documents\Flow`.

If you want them backed up automatically, open **Settings** and change **Data folder** to a path inside OneDrive, e.g. `C:\Users\<you>\OneDrive\Documents\Flow`. Click Save, then close Flow and start it again — your cards now live in that folder.

---

## Daily use

- **Start Flow:** double-click `start.bat`. Browser opens, work in it.
- **Stop Flow:** close the black window.
- **Process meeting notes:** click **Process Notes** in the top right, paste your notes, review the suggested cards, accept what you want.

---

## Something went wrong?

| Symptom | What to try |
|---|---|
| `setup.bat` says Python is missing | Install it from python.org and tick "Add Python to PATH" |
| `start.bat` says Flow has not been set up | Run `setup.bat` first |
| Browser shows "can't reach this page" | Wait 5 seconds and refresh — the server takes a moment to start |
| Process Notes button is greyed out | Open Settings, paste your API token |
| AI features fail with auth error | The token may have expired — generate a new one and paste it in Settings |

If something else breaks, take a screenshot of the black window contents and send it to Andi.

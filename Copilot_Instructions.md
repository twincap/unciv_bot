# Unciv Discord Bot Development Guide

## 🎯 Objective
Create a Discord bot for the game "Unciv" (an open-source Civ V clone). The bot should be able to fetch and display multiplayer game status, current turn, and player statistics.

## 🛠️ Tech Stack
- Python 3.x
- `discord.py` (latest version)
- `requests` (for fetching data from Unciv multiplayer servers)

## 📝 Step-by-Step Instructions for Copilot

**Step 1: Project Setup**
1. Create a `requirements.txt` with `discord.py`, `python-dotenv`, and `requests`.
2. Create a `.env` file structure (do not put real tokens yet).
3. Create `bot.py` and set up the basic Discord bot boilerplate using `commands.Bot` and intents.

**Step 2: Understanding Unciv Multiplayer Data**
- Unciv multiplayer games are often hosted on servers (like `https://uncivserver.xyz`).
- The bot needs a command (e.g., `!unciv status <game_id>`) to query the server.
- Fetch the game file/status from the server using `requests.get()`. (Note: Unciv save files are usually base64 encoded and gzipped JSON. You will need `base64` and `gzip` modules to decode the game state if querying the raw file).

**Step 3: Implement Core Commands**
1. `!ping` - Basic bot health check.
2. `!unciv game <game_id>` - Connects to the Unciv server, downloads the current game state, parses it, and returns:
   - Current Turn number.
   - Which civilization/player's turn it currently is.
   - Basic leaderboard or score if available.

**Step 4: Refinement**
- Wrap the output in a nice Discord Embed (`discord.Embed`).
- Add error handling (invalid game ID, server down, etc.).

**Action Required from Copilot:**
Please read this entire document and start by creating the project structure and `bot.py`. Ask the user for the Discord Bot Token when you are ready to test.
# 🎬 Box Office Discord Bot

A Discord bot with two commands:

- `/box-office <movie>` — Look up the lifetime box office gross for any movie (via TMDB)
- `/weekend` — See this weekend's top 10 box office chart (scraped from Box Office Mojo)
- `/ping` — Check if the bot is alive

---

## Setup Guide

### Step 1: Install Python

Download Python 3.11+ from [python.org](https://www.python.org/downloads/).

**Windows users:** Check the box that says **"Add Python to PATH"** during installation.

Verify it works by opening a terminal and running:

```
python --version
```

### Step 2: Create a Discord Bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **"New Application"** → give it a name like "Box Office Bot"
3. Go to the **Bot** tab on the left
4. Click **"Reset Token"** → copy the token and save it somewhere safe
5. Scroll down and toggle on **Message Content Intent**
6. Go to the **OAuth2** tab
7. Under **OAuth2 URL Generator**, check these scopes:
   - `bot`
   - `applications.commands`
8. Under **Bot Permissions**, check:
   - `Send Messages`
   - `Use Slash Commands`
9. Copy the generated URL at the bottom → paste it in your browser → invite the bot to your server

### Step 3: Get a TMDB API Key

1. Sign up at [themoviedb.org](https://www.themoviedb.org/)
2. Go to **Settings → API**
3. Request an API key (choose Developer, say it's for personal use)
4. Copy the API key (v3 auth)

### Step 4: Set Up the Project

Open a terminal, navigate to this folder, and run:

```
pip install -r requirements.txt
```

Then copy the example environment file and fill in your keys:

```
cp .env.example .env
```

Open `.env` in your editor and replace the placeholder values with your actual Discord token and TMDB API key.

### Step 5: Run the Bot

```
python bot.py
```

You should see:

```
✅ Bot is online as YourBotName#1234
   Slash commands synced — try /ping, /box-office, or /weekend
```

Go to your Discord server and try `/ping` first. Slash commands can take a few minutes to appear the first time.

---

## Troubleshooting

**Slash commands don't show up**
They can take up to an hour to register globally. Wait a bit and restart Discord.

**"DISCORD_TOKEN not found"**
Make sure your `.env` file is in the same folder as `bot.py`, and that there are no extra spaces or quotes around the values.

**Bot goes offline when I close the terminal**
The bot only runs while the script is running. For 24/7 hosting, look into Oracle Cloud free tier, a Raspberry Pi, or Railway.app.

**Weekend chart returns empty**
Box Office Mojo occasionally changes their HTML layout. If the scraper breaks, the column indices in `scrape_weekend_chart()` may need adjusting. Inspect the page source to find the correct positions.

---

## Notes

- The `/box-office` command returns **worldwide lifetime gross** from TMDB. This is not a live daily number — it's updated periodically by the TMDB community.
- The `/weekend` command scrapes Box Office Mojo and caches results for 1 hour to avoid hitting the site too often.
- Box Office Mojo does not have a public API, so the scraper may break if they change their site. This is normal — just update the parsing logic when it happens.

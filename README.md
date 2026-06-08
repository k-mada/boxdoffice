# 🎬 BoxdOffice Discord Bot

A Discord bot that pulls box office data from [Box Office Mojo](https://www.boxofficemojo.com/).

## Commands

- `/ping` — Check that the bot is alive.
- `/boxoffice <movie>` — Look up Domestic / International / Worldwide gross for a movie. Accepts an optional trailing year to disambiguate (e.g. `sabrina 1995`).
- `/weekendtop10 [date]` — Top 10 films for a weekend. With no argument, returns the most recent weekend. With a `MM/DD/YYYY` date, returns the closest weekend on or before that date.
- `/yearlytop10 <year>` — Top 10 grossing films released in a given 4-digit year, ranked by total domestic gross.

All data is scraped from Box Office Mojo. No third-party API keys are needed.

---

## Setup

### 1. Install Python

Python 3.10+ is required (the code uses PEP 604 `X | None` syntax). Get it from [python.org](https://www.python.org/downloads/).

### 2. Create a Discord application

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and click **New Application**.
2. Open the **Bot** tab → **Reset Token** → copy the token somewhere safe. You do *not* need to enable any Privileged Gateway Intents; slash commands don't require Message Content Intent.
3. Open the **OAuth2** tab → **URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot permissions: `Send Messages`, `Embed Links`
4. Open the generated URL and invite the bot to your server.

### 3. Install dependencies

```
pip install -r requirements.txt
```

### 4. Configure the token

Create a `.env` file in the project root:

```
DISCORD_TOKEN=your-token-here
```

### 5. Run the bot

```
python bot.py
```

You should see:

```
✅ Bot is online as YourBotName#1234
   Slash commands synced — try /ping, /boxoffice, /weekendtop10, or /yearlytop10
```

Global slash commands can take up to an hour to propagate the first time you sync them. After that, they appear immediately.

---

## Deployment

A `Procfile` is included for [Railway](https://railway.app/) / Heroku-style worker dynos:

```
worker: python bot.py
```

Set `DISCORD_TOKEN` as an environment variable in the host's dashboard.

---

## Troubleshooting

**Slash commands don't show up**
Global commands take up to an hour the first time. Restart your Discord client after waiting.

**`DISCORD_TOKEN not found`**
The `.env` file must be in the same folder as `bot.py`, with no quotes around the token value.

**`/weekendtop10` or `/boxoffice` returns nothing**
Box Office Mojo occasionally changes its HTML. The scraper relies on column order in the weekend chart table (see `_parse_chart` in `bot.py`) and on label-then-amount text patterns for the title page (see `_bom_scrape_grosses`). Inspect the live page and adjust selectors when this breaks.

**Bot goes offline when the terminal closes**
The bot only runs while the script does. For 24/7 use, deploy to Railway, a Raspberry Pi, or any always-on host.

---

## Notes

- `/weekendtop10` results are cached for 1 hour (default-date only — historical lookups always re-fetch). See `CACHE_DURATION` in `bot.py`.
- `/yearlytop10` ranks by **total domestic lifetime gross** of films released that year, not by gross earned within the calendar year.
- Box Office Mojo has no public API; if they change their site, the scraper will need updates.

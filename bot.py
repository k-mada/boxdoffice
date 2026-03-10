import os
import re
import time
import asyncio
import logging
import datetime
from dataclasses import dataclass
from urllib.parse import quote
import discord
from discord import app_commands
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SYNC_ON_STARTUP = os.getenv("SYNC_ON_STARTUP", "").lower() in ("1", "true", "yes")

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# ---------------------------------------------------------------------------
# Cache for the weekend chart (so we don't scrape on every command)
# ---------------------------------------------------------------------------
@dataclass
class _WeekendCache:
    data: list | None = None
    date_label: str = ""
    timestamp: float = 0.0

_weekend_cache = _WeekendCache()
CACHE_DURATION = 3600  # refresh at most once per hour

# ---------------------------------------------------------------------------
# Rate limiting — adjust these constants to tune allowed command frequency.
# Each value is enforced per user across all servers.
# ---------------------------------------------------------------------------
BOXOFFICE_RATE   = 3     # max uses per window
BOXOFFICE_WINDOW = 60.0  # window size in seconds

WEEKEND_RATE     = 5
WEEKEND_WINDOW   = 60.0


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ---------------------------------------------------------------------------
# Box Office Mojo helpers (used by /boxoffice)
# ---------------------------------------------------------------------------
_MONEY_RE = re.compile(r"^\$[\d,]+$")


def _parse_grosses(html: str) -> dict | None:
    """
    Parse domestic/international/worldwide gross from a BOM title page using
    DOM traversal rather than flattening the page to text lines.
    Returns a dict with title/domestic/international/worldwide, or None if
    none of the gross values could be found.
    """
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.select_one("h1")
    title = h1.get_text(strip=True) if h1 else "Unknown"

    def money_near(tag) -> str | None:
        """Return the first money value in tag's siblings or its parent's siblings."""
        for node in tag.next_siblings:
            if hasattr(node, "get_text") and _MONEY_RE.match(node.get_text(strip=True)):
                return node.get_text(strip=True)
        if tag.parent:
            for node in tag.parent.next_siblings:
                if hasattr(node, "get_text") and _MONEY_RE.match(node.get_text(strip=True)):
                    return node.get_text(strip=True)
        return None

    result = {"title": title, "domestic": None, "international": None, "worldwide": None}

    for node in soup.find_all(string=re.compile(r"^Domestic")):
        if result["domestic"] is None:
            result["domestic"] = money_near(node.parent)

    for node in soup.find_all(string=re.compile(r"^International")):
        if result["international"] is None:
            result["international"] = money_near(node.parent)

    for node in soup.find_all(string=re.compile(r"^Worldwide$")):
        if result["worldwide"] is None:
            result["worldwide"] = money_near(node.parent)

    if not any(result[k] for k in ("domestic", "international", "worldwide")):
        return None
    return result


def _bom_fetch_movie(query: str, year: str | None = None) -> dict | None:
    """
    Search BOM then scrape the title page in a single synchronous chain.
    Returns a dict with title, domestic, international, worldwide, poster_url,
    or None if the movie isn't found or the scrape fails.
    """
    # Step 1: search
    q = f"{query} {year}" if year else query
    search_url = f"https://www.boxofficemojo.com/search/?q={quote(q)}"
    try:
        resp = requests.get(search_url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Error searching BOM for %r: %s", q, e)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    link = soup.find("a", href=re.compile(r"/title/tt\d+/"))
    if not link:
        return None

    path = link["href"].split("?")[0]
    title_url = f"https://www.boxofficemojo.com{path}"
    img = link.find("img")
    poster_url = img["src"] if img and img.get("src") else None

    # Step 2: scrape title page
    try:
        resp = requests.get(title_url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Error fetching BOM title page %s: %s", title_url, e)
        return None

    grosses = _parse_grosses(resp.text)
    if not grosses:
        return None
    return {**grosses, "poster_url": poster_url}


# ---------------------------------------------------------------------------
# Box Office Mojo scraper (used by /weekend)
# ---------------------------------------------------------------------------
def _weekend_url_candidates(target_date: datetime.date | None = None) -> list[str]:
    """
    Return BOM weekend URL candidates most-recent-first.
    BOM weekends run Fri-Sun. We find the most recent Friday on or before
    target_date (defaulting to today) and build ISO-week URLs from there.
    """
    if target_date is None:
        target_date = datetime.date.today()

    # Find the most recent Friday on or before target_date
    # isoweekday: Mon=1 ... Fri=5, Sat=6, Sun=7
    days_since_friday = (target_date.isoweekday() - 5) % 7
    friday = target_date - datetime.timedelta(days=days_since_friday)

    candidates = []
    for delta in (0, -1):
        d = friday + datetime.timedelta(weeks=delta)
        y, w, _ = d.isocalendar()
        candidates.append(f"https://www.boxofficemojo.com/weekend/{y}W{w:02d}/")
    return candidates


def _parse_chart(html: str) -> tuple[list[dict], str]:
    """
    Parse a BOM weekend detail page.
    Column positions are derived from the header row so any BOM schema change
    produces an explicit log message and empty results rather than silent garbage.
    Returns (movies, date_label) where date_label comes from the page <h4>.
    """
    soup = BeautifulSoup(html, "html.parser")

    h4 = soup.select_one("h4")
    date_label = h4.get_text(strip=True) if h4 else ""

    table = soup.select_one("table")
    if not table:
        return [], date_label

    # Detect column indices from the header row
    header_row = table.select_one("tr")
    if not header_row:
        return [], date_label
    headers = [th.get_text(strip=True).lower() for th in header_row.select("th")]
    try:
        title_idx = next(i for i, h in enumerate(headers) if "title" in h)
        gross_idx = next(i for i, h in enumerate(headers) if "gross" in h)
        chg_idx   = next(i for i, h in enumerate(headers) if "%" in h)
        thtr_idx  = next(i for i, h in enumerate(headers) if "thtr" in h or "theater" in h)
    except StopIteration:
        logger.error("Unexpected BOM table headers: %s", headers)
        return [], date_label

    min_cols = max(title_idx, gross_idx, chg_idx, thtr_idx) + 1
    results = []
    for row in table.select("tr")[1:11]:  # top 10 only
        cells = row.select("td")
        if len(cells) < min_cols:
            continue
        change = cells[chg_idx].get_text(strip=True)
        results.append({
            "rank":     cells[0].get_text(strip=True),
            "title":    cells[title_idx].get_text(strip=True),
            "gross":    cells[gross_idx].get_text(strip=True),
            "change":   change or "NEW",
            "theaters": cells[thtr_idx].get_text(strip=True),
        })

    return results, date_label


def _abbrev_gross(gross_str: str) -> str:
    """Convert '$21,706,163' → '$21.7M'."""
    digits = gross_str.replace("$", "").replace(",", "")
    try:
        amount = int(digits)
    except ValueError:
        return gross_str
    if amount >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.2f}B"
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    return f"${amount / 1_000:.0f}K"


def _format_chart_table(movies: list[dict]) -> str:
    """Format the top 10 as a markdown list that reflows on mobile."""
    lines = []
    for m in movies:
        gross = _abbrev_gross(m["gross"])
        chg = m["change"]
        chg_str = " · 🆕" if chg == "NEW" else (f" · {chg}" if chg and chg != "-" else "")
        lines.append(f"**{m['rank']}.** {m['title']} — {gross}{chg_str}")
    return "\n".join(lines)


def _fetch_weekend_chart(target_date: datetime.date | None = None) -> tuple[list[dict], str]:
    """Synchronous fetch — called via asyncio.to_thread so it won't block the event loop."""
    for url in _weekend_url_candidates(target_date):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            resp.raise_for_status()
            movies, date_label = _parse_chart(resp.text)
            if movies:
                return movies, date_label
        except requests.RequestException as e:
            logger.error("Error fetching %s: %s", url, e)
    return [], ""


async def get_weekend_chart(target_date: datetime.date | None = None) -> tuple[list[dict], str]:
    """Return the weekend chart. Uses cache only for the default (most recent) case."""
    if target_date is None:
        now = time.time()
        if _weekend_cache.data and (now - _weekend_cache.timestamp < CACHE_DURATION):
            return _weekend_cache.data, _weekend_cache.date_label

        data, date_label = await asyncio.to_thread(_fetch_weekend_chart)
        if data:
            _weekend_cache.data = data
            _weekend_cache.date_label = date_label
            _weekend_cache.timestamp = now
        return data, date_label

    # Historical lookup — always fetch fresh, no caching
    return await asyncio.to_thread(_fetch_weekend_chart, target_date)


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

@tree.command(name="ping", description="Check if the bot is alive")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong! 🏓")


@tree.command(name="boxoffice", description="Get the box office gross for a movie")
@app_commands.checks.cooldown(BOXOFFICE_RATE, BOXOFFICE_WINDOW, key=lambda i: i.user.id)
@app_commands.describe(movie="Movie name, optionally followed by year (e.g. 'sabrina 1995')")
async def box_office(interaction: discord.Interaction, movie: str):
    await interaction.response.defer()

    # Extract optional trailing year from query
    year = None
    m = re.search(r'\b((?:19|20)\d{2})\s*$', movie)
    if m:
        year = m.group(1)
        movie = movie[:m.start()].strip()

    data = await asyncio.to_thread(_bom_fetch_movie, movie, year)

    if not data:
        await interaction.followup.send(f"Couldn't find **{movie}** on Box Office Mojo.")
        return

    embed = discord.Embed(
        title=f"🎬 {data['title']}",
        color=discord.Color.gold(),
    )
    embed.add_field(name="Domestic", value=data["domestic"] or "N/A", inline=True)
    embed.add_field(name="International", value=data["international"] or "N/A", inline=True)
    embed.add_field(name="Worldwide", value=data["worldwide"] or "N/A", inline=True)
    if data["poster_url"]:
        embed.set_thumbnail(url=data["poster_url"])
    embed.set_footer(text="Source: boxofficemojo.com")
    await interaction.followup.send(embed=embed)


@tree.command(name="weekendtop10", description="Get a weekend's top 10 box office films")
@app_commands.checks.cooldown(WEEKEND_RATE, WEEKEND_WINDOW, key=lambda i: i.user.id)
@app_commands.describe(date="Date in MM/DD/YYYY format — returns the closest weekend on or before that date")
async def weekend(interaction: discord.Interaction, date: str | None = None):
    target_date = None
    if date is not None:
        try:
            target_date = datetime.datetime.strptime(date, "%m/%d/%Y").date()
        except ValueError:
            await interaction.response.send_message("Use the date format MM/DD/YYYY")
            return

    await interaction.response.defer()

    movies, date_label = await get_weekend_chart(target_date)

    if not movies:
        await interaction.followup.send(
            "Couldn't fetch the chart for that date. Box Office Mojo may not have data that far back, or the page layout changed."
        )
        return

    embed = discord.Embed(
        title=f"🍿 Weekend Box Office Top 10 — {date_label}",
        color=discord.Color.blue(),
    )
    embed.description = _format_chart_table(movies)
    embed.set_footer(text="Source: boxofficemojo.com")
    await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------------------
# Bot startup
# ---------------------------------------------------------------------------

@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            f"Too many people are accessing BoxdOffice right now. Try again in {error.retry_after:.0f}s.",
            ephemeral=True,
        )
    else:
        logger.error("Unhandled app command error in /%s: %s", interaction.command and interaction.command.name, error)
        msg = "Something went wrong. Please try again."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


@client.event
async def on_ready():
    if SYNC_ON_STARTUP:
        await tree.sync()
        logger.info("Slash commands synced.")
    logger.info("Bot is online as %s", client.user)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not DISCORD_TOKEN:
        logger.critical("DISCORD_TOKEN not found. Make sure your .env file is set up.")
    else:
        client.run(DISCORD_TOKEN)

import os
import re
import time
import asyncio
import datetime
import discord
from discord import app_commands
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# ---------------------------------------------------------------------------
# Cache for the weekend chart (so we don't scrape on every command)
# ---------------------------------------------------------------------------
weekend_cache = {
    "data": None,
    "timestamp": 0,
}
CACHE_DURATION = 3600  # refresh at most once per hour


# ---------------------------------------------------------------------------
# TMDB helpers (used by /box-office)
# ---------------------------------------------------------------------------
def search_movie(query: str) -> dict | None:
    """
    Search TMDB for a movie, then fetch its full details (which include revenue).
    If the query ends with a 4-digit year (e.g. "sabrina 1995"), it is extracted
    and passed as primary_release_year for a more precise match.
    """
    # Extract a trailing year like "sabrina 1995" → query="sabrina", year=1995
    year = None
    match = re.search(r'\b((?:19|20)\d{2})\s*$', query)
    if match:
        year = match.group(1)
        query = query[:match.start()].strip()

    search_url = "https://api.themoviedb.org/3/search/movie"
    params = {"api_key": TMDB_API_KEY, "query": query}
    if year:
        params["primary_release_year"] = year
    results = requests.get(search_url, params=params).json().get("results", [])

    # If no results with the year filter, fall back to an unfiltered search
    if not results and year:
        params.pop("primary_release_year")
        results = requests.get(search_url, params=params).json().get("results", [])

    if not results:
        return None

    # Take the top result and fetch full details (search results don't include revenue)
    movie_id = results[0]["id"]
    details_url = f"https://api.themoviedb.org/3/movie/{movie_id}"
    details = requests.get(details_url, params={"api_key": TMDB_API_KEY}).json()
    return details


def format_currency(amount: int) -> str:
    """Turn a raw dollar amount into a readable string like $1.2B or $345.6M."""
    if amount >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.2f}B"
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    return f"${amount:,}"


# ---------------------------------------------------------------------------
# Box Office Mojo scraper (used by /weekend)
# ---------------------------------------------------------------------------
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def _weekend_url_candidates() -> list[str]:
    """
    Return BOM weekend URL candidates based on today's date, most recent first.
    BOM uses ISO week numbers: /weekend/{YEAR}W{WEEK:02d}/
    If today is Mon-Thu the last full weekend is last week; Fri-Sun it may be this week.
    We yield the current week then the previous week as a fallback.
    """
    today = datetime.date.today()
    candidates = []
    for delta in (0, -1):
        d = today + datetime.timedelta(weeks=delta)
        y, w, _ = d.isocalendar()
        candidates.append(f"https://www.boxofficemojo.com/weekend/{y}W{w:02d}/")
    return candidates


def _parse_chart(html: str) -> tuple[list[dict], str]:
    """
    Parse a BOM weekend detail page.
    Column order: 0 Rank | 1 LW | 2 Title | 3 Weekend Gross | 4 %Chg |
                  5 Theaters | 6 Thtr Chg | 7 Per-Thtr Avg | 8 Total Gross | 9 Weeks
    Returns (movies, date_label) where date_label comes from the page <h4>.
    """
    soup = BeautifulSoup(html, "html.parser")

    # h4 contains the human-readable date range e.g. "February 27-March 1, 2026"
    h4 = soup.select_one("h4")
    date_label = h4.get_text(strip=True) if h4 else ""

    table = soup.select_one("table")
    if not table:
        return [], date_label

    results = []
    for row in table.select("tr")[1:11]:  # top 10 only
        cells = row.select("td")
        if len(cells) < 6:
            continue
        change = cells[4].get_text(strip=True) if len(cells) > 4 else ""
        results.append({
            "rank": cells[0].get_text(strip=True),
            "title": cells[2].get_text(strip=True),
            "gross": cells[3].get_text(strip=True),
            "change": change or "NEW",
            "theaters": cells[5].get_text(strip=True),
        })

    return results, date_label


def _format_chart_table(movies: list[dict]) -> str:
    """Format the top 10 as a fixed-width code block table for Discord."""
    TITLE_W = 20
    GROSS_W = 10
    THTR_W  = 6
    CHG_W   = 8

    header = f"{'#':>2}  {'Title':<{TITLE_W}}  {'Gross':>{GROSS_W}}  {'Thtr':>{THTR_W}}  {'Chg':>{CHG_W}}"
    sep    = "─" * len(header)

    lines = [header, sep]
    for m in movies:
        title = m["title"]
        if len(title) > TITLE_W:
            title = title[:TITLE_W - 1] + "…"
        lines.append(
            f"{m['rank']:>2}  {title:<{TITLE_W}}  {m['gross']:>{GROSS_W}}  {m['theaters']:>{THTR_W}}  {m['change']:>{CHG_W}}"
        )

    return "```\n" + "\n".join(lines) + "\n```"


def _fetch_weekend_chart() -> tuple[list[dict], str]:
    """Synchronous fetch — called via asyncio.to_thread so it won't block the event loop."""
    for url in _weekend_url_candidates():
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            resp.raise_for_status()
            movies, date_label = _parse_chart(resp.text)
            if movies:
                return movies, date_label
        except requests.RequestException as e:
            print(f"Error fetching {url}: {e}")
    return [], ""


async def get_weekend_chart() -> tuple[list[dict], str]:
    """Return the weekend chart, using the cache if still fresh."""
    now = time.time()
    if weekend_cache["data"] and (now - weekend_cache["timestamp"] < CACHE_DURATION):
        return weekend_cache["data"], weekend_cache.get("date_label", "")

    data, date_label = await asyncio.to_thread(_fetch_weekend_chart)
    if data:
        weekend_cache["data"] = data
        weekend_cache["date_label"] = date_label
        weekend_cache["timestamp"] = now
    return data, date_label


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

@tree.command(name="ping", description="Check if the bot is alive")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong! 🏓")


@tree.command(name="boxoffice", description="Get the box office gross for a movie")
@app_commands.describe(movie="Movie name, optionally followed by year (e.g. 'sabrina 1995')")
async def box_office(interaction: discord.Interaction, movie: str):
    await interaction.response.defer()  # gives us time to call the API

    data = search_movie(movie)

    if not data:
        await interaction.followup.send(f"Couldn't find a movie matching **{movie}**.")
        return

    title = data.get("title", "Unknown")
    revenue = data.get("revenue", 0)
    budget = data.get("budget", 0)
    release = data.get("release_date", "N/A")
    poster_path = data.get("poster_path")

    embed = discord.Embed(
        title=f"🎬 {title}",
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="Box Office Gross",
        value=format_currency(revenue) if revenue else "Not reported",
        inline=True,
    )
    embed.add_field(
        name="Budget",
        value=format_currency(budget) if budget else "Not reported",
        inline=True,
    )
    embed.add_field(name="Release Date", value=release, inline=True)

    if revenue and budget:
        profit = revenue - budget
        label = "Profit" if profit >= 0 else "Loss"
        embed.add_field(
            name=f"{label} (Gross − Budget)",
            value=format_currency(abs(profit)),
            inline=False,
        )

    if poster_path:
        embed.set_thumbnail(url=f"https://image.tmdb.org/t/p/w200{poster_path}")

    embed.set_footer(text="Data from TMDB • Revenue = worldwide lifetime gross")
    await interaction.followup.send(embed=embed)


@tree.command(name="weekendtop10", description="Get this weekend's top 10 box office films")
async def weekend(interaction: discord.Interaction):
    await interaction.response.defer()

    movies, date_label = await get_weekend_chart()

    if not movies:
        await interaction.followup.send(
            "Couldn't fetch this weekend's chart. Box Office Mojo may be down or the page layout changed."
        )
        return

    embed = discord.Embed(
        title=f"🍿 Weekend Box Office Top 10 — {date_label}",
        color=discord.Color.blue(),
    )
    embed.description = _format_chart_table(movies)
    embed.set_footer(text="Data scraped from Box Office Mojo")
    await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------------------
# Bot startup
# ---------------------------------------------------------------------------

@client.event
async def on_ready():
    await tree.sync()
    print(f"✅ Bot is online as {client.user}")
    print(f"   Slash commands synced — try /ping, /boxoffice, or /weekendtop10")


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ Error: DISCORD_TOKEN not found. Make sure your .env file is set up.")
    elif not TMDB_API_KEY:
        print("❌ Error: TMDB_API_KEY not found. Make sure your .env file is set up.")
    else:
        client.run(DISCORD_TOKEN)

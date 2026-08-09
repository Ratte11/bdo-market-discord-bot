import os
import json
import asyncio
import logging
from pathlib import Path
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID_RAW = os.getenv("GUILD_ID", "").strip()
GUILD_ID = int(GUILD_ID_RAW) if GUILD_ID_RAW.isdigit() else None

ARSHA_BASE = os.getenv(
    "ARSHA_API_BASE",
    "https://api-arsha-io-2.onrender.com"
).rstrip("/")

REGION = os.getenv("BDO_REGION", "eu").strip().lower()
LANG = os.getenv("BDO_LANG", "en").strip()
CHECK_INTERVAL = int(os.getenv("WATCH_INTERVAL_SECONDS", "1"))

WATCHLIST_FILE = Path(__file__).with_name("watchlist.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("bdo-bot")

if not TOKEN:
    raise SystemExit("DISCORD_TOKEN fehlt in .env")


def load_watchlist() -> dict:
    if not WATCHLIST_FILE.exists():
        return {}
    try:
        data = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        log.exception("watchlist.json konnte nicht geladen werden.")
        return {}


def save_watchlist(data: dict):
    temp = WATCHLIST_FILE.with_suffix(".tmp")
    temp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temp.replace(WATCHLIST_FILE)


def format_silver(value) -> str:
    if value is None:
        return "?"
    try:
        return f"{int(value):,}".replace(",", ".") + " Silber"
    except Exception:
        return str(value)


class ArshaClient:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None

    async def start(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20),
                headers={
                    "Accept": "application/json",
                    "User-Agent": "BDO-Market-Discord-Bot/1.0",
                },
            )
            log.info("Arsha Client gestartet: %s", ARSHA_BASE)

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def _request_json(self, method: str, path: str, **kwargs):
        await self.start()
        url = f"{ARSHA_BASE}{path}"
        async with self.session.request(method, url, **kwargs) as resp:
            text = await resp.text()
            log.info(
                "Arsha %s %s status=%s body=%r",
                method,
                path,
                resp.status,
                text[:500],
            )
            if resp.status != 200:
                raise RuntimeError(
                    f"Arsha HTTP {resp.status}: {text[:300]}"
                )
            try:
                return await resp.json(content_type=None)
            except Exception:
                raise RuntimeError(
                    f"Arsha liefert kein JSON: {text[:300]}"
                )

    async def get_item(self, item_id: int, sid: int = 0) -> dict:
        attempts = [
            (
                "GET",
                f"/v2/{REGION}/item",
                {"params": {"id": item_id, "sid": sid, "lang": LANG}},
            ),
            (
                "GET",
                f"/v2/{REGION}/GetWorldMarketSubList",
                {"params": {"id": item_id, "lang": LANG}},
            ),
            (
                "POST",
                f"/v2/{REGION}/GetWorldMarketSubList",
                {"headers": {"id": str(item_id), "lang": LANG}},
            ),
        ]

        errors = []

        for method, path, kwargs in attempts:
            try:
                data = await self._request_json(method, path, **kwargs)
                item = self._parse_item(data, item_id, sid)
                if item:
                    return item
            except Exception as exc:
                errors.append(str(exc))

        return {
            "ok": False,
            "item_id": item_id,
            "error": " | ".join(errors[-3:]) or "Keine Daten erhalten.",
        }

    def _parse_item(self, data, item_id: int, sid: int):
        if isinstance(data, list):
            candidates = [x for x in data if isinstance(x, dict)]
        elif isinstance(data, dict):
            if isinstance(data.get("detailList"), list):
                candidates = [x for x in data["detailList"] if isinstance(x, dict)]
            elif isinstance(data.get("items"), list):
                candidates = [x for x in data["items"] if isinstance(x, dict)]
            else:
                candidates = [data]
        else:
            return None

        if not candidates:
            return None

        selected = None
        for entry in candidates:
            entry_sid = entry.get("sid") if entry.get("sid") is not None else entry.get("subKey", 0)
            try:
                if int(entry_sid or 0) == int(sid):
                    selected = entry
                    break
            except Exception:
                pass

        if selected is None:
            selected = candidates[0]

        name = selected.get("name") or selected.get("itemName") or f"Item {item_id}"

        price = self._first_int(
            selected,
            "currentPrice",
            "price",
            "basePrice",
            "lastSoldPrice",
            "pricePerOne",
        )
        stock = self._first_int(
            selected,
            "currentStock",
            "amountListed",
            "stock",
            "count",
        )
        trades = self._first_int(
            selected,
            "totalTrades",
            "totalTradeCount",
            "tradeCount",
        )

        return {
            "ok": price is not None,
            "item_id": item_id,
            "sid": sid,
            "name": name,
            "price": price,
            "stock": stock,
            "total_trades": trades,
            "raw": data,
        }

    @staticmethod
    def _first_int(data: dict, *fields):
        for field in fields:
            value = data.get(field)
            if value is None:
                continue
            try:
                return int(value)
            except Exception:
                continue
        return None


class BDOBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=discord.Intents.default(),
        )
        self.arsha = ArshaClient()
        self.watchlist = load_watchlist()

    async def setup_hook(self):
        await self.arsha.start()

        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info(
                "%d Commands auf Guild %s synchronisiert.",
                len(synced),
                GUILD_ID,
            )
        else:
            synced = await self.tree.sync()
            log.info("%d globale Commands synchronisiert.", len(synced))

        if not self.watch_loop.is_running():
            self.watch_loop.start()

    async def close(self):
        if self.watch_loop.is_running():
            self.watch_loop.cancel()
        await self.arsha.close()
        await super().close()

    @tasks.loop(seconds=CHECK_INTERVAL)
    async def watch_loop(self):
        if not self.watchlist:
            return

        log.info("WATCHLIST CHECK: %d Items", len(self.watchlist))
        changed = False

        for watch_id, watch in list(self.watchlist.items()):
            try:
                item_id = int(watch["item_id"])
                sid = int(watch.get("sid", 0))

                result = await self.arsha.get_item(item_id, sid)

                if not result.get("ok"):
                    log.warning(
                        "Item %s konnte nicht geladen werden: %s",
                        item_id,
                        result.get("error"),
                    )
                    continue

                new_price = result.get("price")
                new_stock = result.get("stock")

                old_price = watch.get("last_price")

                name = result.get(
                    "name",
                    watch.get("name", f"Item {item_id}")
                )

                if old_price is None:
                    watch["last_price"] = new_price
                    watch["last_stock"] = new_stock
                    watch["name"] = name
                    changed = True
                    continue

                if (
                    new_price is not None
                    and old_price is not None
                    and int(new_price) > int(old_price)
                ):
                    difference = int(new_price) - int(old_price)
                    percent = (
                        difference / int(old_price) * 100
                        if int(old_price) > 0
                        else 0
                    )

                    channel_id = int(watch["channel_id"])
                    user_id = int(watch["user_id"])

                    channel = self.get_channel(channel_id)
                    if channel is None:
                        try:
                            channel = await self.fetch_channel(channel_id)
                        except Exception:
                            channel = None

                    if channel:
                        embed = discord.Embed(
                            title="📈 BDO Preis gestiegen!",
                            color=discord.Color.green(),
                        )
                        embed.add_field(
                            name="Item",
                            value=f"**{name}**",
                            inline=False,
                        )
                        embed.add_field(
                            name="Alter Preis",
                            value=format_silver(old_price),
                            inline=True,
                        )
                        embed.add_field(
                            name="Neuer Preis",
                            value=format_silver(new_price),
                            inline=True,
                        )
                        embed.add_field(
                            name="Anstieg",
                            value=(
                                f"+{format_silver(difference)}\n"
                                f"+{percent:.2f}%"
                            ),
                            inline=True,
                        )

                        if new_stock is not None:
                            embed.add_field(
                                name="Bestand",
                                value=str(new_stock),
                                inline=True,
                            )

                        embed.set_footer(
                            text=(
                                f"Item ID {item_id} • "
                                f"SID {sid} • "
                                f"{REGION.upper()}"
                            )
                        )

                        await channel.send(
                            content=f"<@{user_id}> 🔔",
                            embed=embed,
                            allowed_mentions=discord.AllowedMentions(
                                users=True
                            ),
                        )

                        log.info(
                            "PRICE ALERT: %s %s -> %s",
                            item_id,
                            old_price,
                            new_price,
                        )

                watch["last_price"] = new_price
                watch["last_stock"] = new_stock
                watch["name"] = name
                changed = True

            except Exception:
                log.exception("Fehler bei Watch %s", watch_id)

        if changed:
            save_watchlist(self.watchlist)

    @watch_loop.before_loop
    async def before_watch_loop(self):
        await self.wait_until_ready()


bot = BDOBot()


@bot.event
async def on_ready():
    log.info("Eingeloggt als %s (ID %s)", bot.user, bot.user.id)
    log.info(
        "Watchlist: %d Items | Intervall: %d Sek.",
        len(bot.watchlist),
        CHECK_INTERVAL,
    )


@bot.tree.command(
    name="price",
    description="Aktuellen BDO-Preis und Bestand anzeigen",
)
@app_commands.describe(
    item_id="BDO Item-ID",
    sid="Enhancement/Sub-ID, meistens 0",
)
async def price(
    interaction: discord.Interaction,
    item_id: int,
    sid: int = 0,
):
    await interaction.response.defer()

    result = await bot.arsha.get_item(item_id, sid)

    if not result.get("ok"):
        await interaction.followup.send(
            f"❌ API-Fehler: `{result.get('error')}`"
        )
        return

    embed = discord.Embed(
        title=result["name"],
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="Preis",
        value=format_silver(result["price"]),
        inline=True,
    )
    embed.add_field(
        name="Bestand",
        value=str(
            result["stock"]
            if result.get("stock") is not None
            else "?"
        ),
        inline=True,
    )
    embed.add_field(
        name="Trades",
        value=str(
            result["total_trades"]
            if result.get("total_trades") is not None
            else "?"
        ),
        inline=True,
    )
    embed.set_footer(
        text=f"Item ID {item_id} • SID {sid} • {REGION.upper()}"
    )

    await interaction.followup.send(embed=embed)


@bot.tree.command(
    name="watch",
    description="Bei jedem Preisanstieg dieses Items pingen",
)
@app_commands.describe(
    item_id="BDO Item-ID",
    sid="Enhancement/Sub-ID, meistens 0",
)
async def watch(
    interaction: discord.Interaction,
    item_id: int,
    sid: int = 0,
):
    await interaction.response.defer(ephemeral=True)

    result = await bot.arsha.get_item(item_id, sid)

    if not result.get("ok"):
        await interaction.followup.send(
            f"❌ Item konnte nicht geladen werden: "
            f"`{result.get('error')}`"
        )
        return

    watch_id = f"{interaction.user.id}:{item_id}:{sid}"

    bot.watchlist[watch_id] = {
        "user_id": interaction.user.id,
        "channel_id": interaction.channel_id,
        "item_id": item_id,
        "sid": sid,
        "name": result["name"],
        "last_price": result["price"],
        "last_stock": result.get("stock"),
    }

    save_watchlist(bot.watchlist)

    await interaction.followup.send(
        f"✅ **{result['name']}** wird jetzt überwacht.\n\n"
        f"Preis: **{format_silver(result['price'])}**\n"
        f"Bestand: **{result.get('stock', '?')}**\n"
        f"Check: **alle {CHECK_INTERVAL} Sekunde(n)**\n"
        f"🔔 Ping nur wenn der Preis **steigt**."
    )


@bot.tree.command(
    name="unwatch",
    description="Item aus deiner Watchlist entfernen",
)
@app_commands.describe(
    item_id="BDO Item-ID",
    sid="Enhancement/Sub-ID, meistens 0",
)
async def unwatch(
    interaction: discord.Interaction,
    item_id: int,
    sid: int = 0,
):
    watch_id = f"{interaction.user.id}:{item_id}:{sid}"

    if watch_id not in bot.watchlist:
        await interaction.response.send_message(
            "❌ Dieses Item ist nicht in deiner Watchlist.",
            ephemeral=True,
        )
        return

    old = bot.watchlist.pop(watch_id)
    save_watchlist(bot.watchlist)

    await interaction.response.send_message(
        f"✅ **{old.get('name', item_id)}** entfernt.",
        ephemeral=True,
    )


@bot.tree.command(
    name="watchlist",
    description="Deine überwachten BDO-Items anzeigen",
)
async def watchlist(interaction: discord.Interaction):
    entries = [
        x for x in bot.watchlist.values()
        if int(x.get("user_id", 0)) == interaction.user.id
    ]

    if not entries:
        await interaction.response.send_message(
            "📭 Deine Watchlist ist leer.",
            ephemeral=True,
        )
        return

    lines = []

    for item in entries:
        lines.append(
            f"**{item.get('name', 'Unknown')}**\n"
            f"ID `{item['item_id']}` • SID `{item.get('sid', 0)}`\n"
            f"Letzter Preis: **{format_silver(item.get('last_price'))}**\n"
            f"Bestand: **{item.get('last_stock', '?')}**"
        )

    await interaction.response.send_message(
        "\n\n".join(lines),
        ephemeral=True,
    )


@bot.tree.command(
    name="testping",
    description="Testet deinen Discord-Ping",
)
async def testping(interaction: discord.Interaction):
    await interaction.response.send_message(
        "✅ Test-Ping wird gesendet.",
        ephemeral=True,
    )

    await interaction.channel.send(
        f"<@{interaction.user.id}> 🔔 **BDO Market Test-Ping erfolgreich!**",
        allowed_mentions=discord.AllowedMentions(users=True),
    )


@bot.tree.command(
    name="testapi",
    description="Testet deine Arsha-API mit Item 820953",
)
async def testapi(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    result = await bot.arsha.get_item(820953, 0)

    if not result.get("ok"):
        await interaction.followup.send(
            f"❌ API-Test fehlgeschlagen:\n`{result.get('error')}`"
        )
        return

    await interaction.followup.send(
        "✅ **API funktioniert**\n\n"
        f"Item: **{result['name']}**\n"
        f"Preis: **{format_silver(result['price'])}**\n"
        f"Bestand: **{result.get('stock', '?')}**"
    )


async def main():
    try:
        await bot.start(TOKEN)
    finally:
        await bot.arsha.close()


if __name__ == "__main__":
    asyncio.run(main())

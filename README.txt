BDO MARKET DISCORD BOT
======================

API:
https://api-arsha-io-2.onrender.com

SETUP
-----

1. Ordner entpacken.
2. start.bat einmal starten.
3. Die automatisch erzeugte .env öffnen.
4. DISCORD_TOKEN und GUILD_ID eintragen.
5. start.bat erneut starten.

COMMANDS
--------

/testapi
Testet deine Arsha-API mit Item 820953.

/testping
Testet Discord-Pings.

/price <item_id> [sid]
Zeigt Preis, Bestand und Trades.

/watch <item_id> [sid]
Überwacht ein Item.
Der Bot pingt dich nur wenn der neue Preis HÖHER als der vorige Preis ist.

/unwatch <item_id> [sid]
Entfernt ein Item.

/watchlist
Zeigt deine Watchlist.

INTERVALL
---------

WATCH_INTERVAL_SECONDS=1

Das bedeutet: alle 1 Sekunde prüfen.

WATCHLIST
---------

Die Watchlist wird in watchlist.json gespeichert.

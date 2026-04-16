from __future__ import annotations

import asyncio
import base64
import binascii
import gzip
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import discord
import requests
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# Prefer .env values over inherited shell/system variables during local runs.
load_dotenv(override=True)

TURN_KEYS = ("currentTurn", "turn", "turns", "current_turn")
CURRENT_PLAYER_KEYS = (
    "currentPlayer",
    "current_player",
    "currentPlayerName",
    "current_player_name",
    "currentCiv",
    "currentPlayerCiv",
    "civilizationToMove",
)
PLAYER_LIST_KEYS = ("players", "civilizations", "civs", "nations")
PLAYER_NAME_KEYS = ("playerName", "name", "civilizationName", "civName", "civ", "nation")
PLAYER_SCORE_KEYS = ("score", "points", "victoryPoints")
DEFAULT_TRACK_FILE = "tracked_games.json"
TRACK_INTERVAL_MIN_SECONDS = 30
TRACK_INTERVAL_MAX_SECONDS = 1800


class UncivAPIError(Exception):
    """Raised when game data cannot be fetched or parsed."""


@dataclass(slots=True)
class ScoreEntry:
    name: str
    score: int | None


@dataclass(slots=True)
class UncivGameStatus:
    game_id: str
    source_url: str
    turn: int | None
    current_player: str | None
    leaderboard: list[ScoreEntry]


@dataclass(slots=True)
class ServerHealthStatus:
    base_url: str
    api_version: str
    endpoint: str
    auth_version: int | None = None
    chat_version: int | None = None
    version: int | None = None


@dataclass(slots=True)
class TrackedGame:
    channel_id: int
    game_id: str
    alias: str | None = None
    last_state: str | None = None
    last_turn: int | None = None
    last_current_player: str | None = None

    @property
    def display_name(self) -> str:
        return self.alias or self.game_id


class UncivClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: int,
        url_template: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.url_template = (url_template or "").strip() or None
        self.session = session or requests.Session()

    def fetch_game_status(self, game_id: str) -> UncivGameStatus:
        last_error = ""

        for url in self._candidate_urls(game_id):
            try:
                response = self.session.get(
                    url,
                    timeout=self.timeout_seconds,
                    headers={"Accept": "application/json,text/plain,*/*"},
                )
            except requests.RequestException as exc:
                last_error = f"Request failed for {url}: {exc}"
                continue

            if response.status_code == 404:
                last_error = f"Game not found at {url}"
                continue

            if response.status_code >= 400:
                last_error = f"Server returned HTTP {response.status_code} for {url}"
                continue

            try:
                payload = _parse_payload(response.content)
            except ValueError as exc:
                last_error = f"Invalid payload at {url}: {exc}"
                continue

            return _extract_game_status(payload, game_id, url)

        if not last_error:
            last_error = "No valid response from Unciv server."
        raise UncivAPIError(last_error)

    def _candidate_urls(self, game_id: str) -> list[str]:
        if self.url_template and "{game_id}" in self.url_template:
            return [self.url_template.format(game_id=game_id)]

        return [
            f"{self.base_url}/files/{game_id}",
            f"{self.base_url}/game/{game_id}",
            f"{self.base_url}/games/{game_id}",
            f"{self.base_url}/status/{game_id}",
        ]

    def probe_server(self) -> ServerHealthStatus:
        errors: list[str] = []

        isalive_url = f"{self.base_url}/isalive"
        try:
            response = self.session.get(isalive_url, timeout=self.timeout_seconds)
            if response.status_code < 400:
                text = response.text.strip()
                if text.lower().startswith("true"):
                    return ServerHealthStatus(
                        base_url=self.base_url,
                        api_version="APIv1",
                        endpoint=isalive_url,
                    )

                parsed = _try_json_parse(text)
                if isinstance(parsed, dict):
                    return ServerHealthStatus(
                        base_url=self.base_url,
                        api_version="APIv1",
                        endpoint=isalive_url,
                        auth_version=_to_int(parsed.get("authVersion")),
                        chat_version=_to_int(parsed.get("chatVersion")),
                    )

                return ServerHealthStatus(
                    base_url=self.base_url,
                    api_version="APIv1",
                    endpoint=isalive_url,
                )

            errors.append(f"{isalive_url} returned HTTP {response.status_code}")
        except requests.RequestException as exc:
            errors.append(f"{isalive_url} failed: {exc}")

        version_url = f"{self.base_url}/api/version"
        try:
            response = self.session.get(version_url, timeout=self.timeout_seconds)
            if response.status_code < 400:
                parsed = _try_json_parse(response.text)
                version = _to_int(parsed.get("version")) if isinstance(parsed, dict) else None
                if version is None:
                    return ServerHealthStatus(
                        base_url=self.base_url,
                        api_version="APIv2",
                        endpoint=version_url,
                    )

                api_label = "APIv2" if version == 2 else f"APIv{version}"
                return ServerHealthStatus(
                    base_url=self.base_url,
                    api_version=api_label,
                    endpoint=version_url,
                    version=version,
                )

            errors.append(f"{version_url} returned HTTP {response.status_code}")
        except requests.RequestException as exc:
            errors.append(f"{version_url} failed: {exc}")

        raise UncivAPIError("; ".join(errors) if errors else "Unable to detect server API.")


def _parse_payload(content: bytes) -> dict[str, Any]:
    parsed = _decode_payload_recursive(content)
    if not isinstance(parsed, dict):
        raise ValueError("Decoded payload is not a JSON object")
    return parsed


def _decode_payload_recursive(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        raise ValueError("Exceeded payload decoding depth")

    if isinstance(value, bytes):
        if value[:2] == b"\x1f\x8b":
            try:
                return _decode_payload_recursive(gzip.decompress(value), depth + 1)
            except OSError:
                pass

        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Binary payload is not UTF-8") from exc
        return _decode_payload_recursive(text, depth + 1)

    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("Empty payload string")

        json_obj = _try_json_parse(text)
        if json_obj is not None:
            return _decode_payload_recursive(json_obj, depth + 1)

        decoded_bytes = _try_base64_decode(text)
        if decoded_bytes is None:
            raise ValueError("Payload is neither JSON text nor base64")

        return _decode_payload_recursive(decoded_bytes, depth + 1)

    if isinstance(value, dict):
        # Some endpoints wrap the save payload in one of these fields.
        for key in ("save", "gameData", "data", "content", "payload", "file"):
            nested = value.get(key)
            if isinstance(nested, (str, bytes)):
                try:
                    return _decode_payload_recursive(nested, depth + 1)
                except ValueError:
                    continue
        return value

    if isinstance(value, list):
        return {"items": value}

    raise ValueError(f"Unsupported payload type: {type(value)!r}")


def _try_json_parse(text: str) -> Any | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _try_base64_decode(text: str) -> bytes | None:
    padded = text + "=" * (-len(text) % 4)
    try:
        return base64.b64decode(padded, validate=False)
    except (ValueError, binascii.Error):
        return None


def _extract_game_status(payload: dict[str, Any], game_id: str, source_url: str) -> UncivGameStatus:
    turn = _to_int(_find_first_value(payload, TURN_KEYS))
    current_player = _to_text(_find_first_value(payload, CURRENT_PLAYER_KEYS))
    leaderboard = _extract_leaderboard(payload)

    return UncivGameStatus(
        game_id=game_id,
        source_url=source_url,
        turn=turn,
        current_player=current_player,
        leaderboard=leaderboard,
    )


def _find_first_value(node: Any, keys: tuple[str, ...]) -> Any | None:
    if isinstance(node, dict):
        for key in keys:
            if key in node and node[key] not in (None, ""):
                return node[key]
        for value in node.values():
            found = _find_first_value(value, keys)
            if found not in (None, ""):
                return found
        return None

    if isinstance(node, list):
        for item in node:
            found = _find_first_value(item, keys)
            if found not in (None, ""):
                return found
        return None

    return None


def _find_first_list(node: Any, keys: tuple[str, ...]) -> list[Any] | None:
    if isinstance(node, dict):
        for key in keys:
            value = node.get(key)
            if isinstance(value, list):
                return value
        for value in node.values():
            found = _find_first_list(value, keys)
            if isinstance(found, list):
                return found
        return None

    if isinstance(node, list):
        for item in node:
            found = _find_first_list(item, keys)
            if isinstance(found, list):
                return found
        return None

    return None


def _extract_leaderboard(payload: dict[str, Any]) -> list[ScoreEntry]:
    player_list = _find_first_list(payload, PLAYER_LIST_KEYS)
    if not isinstance(player_list, list):
        return []

    entries_by_name: dict[str, ScoreEntry] = {}

    for item in player_list:
        if not isinstance(item, dict):
            continue

        name = _extract_player_name(item)
        if not name:
            continue

        score = _extract_player_score(item)
        existing = entries_by_name.get(name)

        if existing is None:
            entries_by_name[name] = ScoreEntry(name=name, score=score)
            continue

        existing_score = -1 if existing.score is None else existing.score
        new_score = -1 if score is None else score
        if new_score > existing_score:
            entries_by_name[name] = ScoreEntry(name=name, score=score)

    sorted_entries = sorted(
        entries_by_name.values(),
        key=lambda entry: -1 if entry.score is None else entry.score,
        reverse=True,
    )

    return sorted_entries[:10]


def _extract_player_name(player_data: dict[str, Any]) -> str | None:
    for key in PLAYER_NAME_KEYS:
        value = player_data.get(key)
        text = _to_text(value)
        if text:
            return text
    return None


def _extract_player_score(player_data: dict[str, Any]) -> int | None:
    for key in PLAYER_SCORE_KEYS:
        if key in player_data:
            score = _to_int(player_data.get(key))
            if score is not None:
                return score

    stats = player_data.get("stats")
    if isinstance(stats, dict):
        return _to_int(_find_first_value(stats, PLAYER_SCORE_KEYS))

    return None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None

    if isinstance(value, bool):
        return int(value)

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return int(value)

    if isinstance(value, str):
        digits = re.sub(r"[^0-9-]", "", value)
        if not digits or digits == "-":
            return None
        try:
            return int(digits)
        except ValueError:
            return None

    return None


def _to_text(value: Any) -> str | None:
    if value is None:
        return None

    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None

    if isinstance(value, (int, float)):
        return str(value)

    return None


def _build_status_embed(status: UncivGameStatus) -> discord.Embed:
    embed = discord.Embed(
        title=f"Unciv Game Status: {status.game_id}",
        color=discord.Color.blurple(),
    )

    embed.add_field(
        name="Current Turn",
        value=str(status.turn) if status.turn is not None else "Unknown",
        inline=True,
    )

    embed.add_field(
        name="Current Player",
        value=status.current_player or "Unknown",
        inline=True,
    )

    if status.leaderboard:
        lines = []
        for index, entry in enumerate(status.leaderboard, start=1):
            score_text = "N/A" if entry.score is None else str(entry.score)
            lines.append(f"{index}. {entry.name}: {score_text}")
        embed.add_field(name="Leaderboard", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Leaderboard", value="No score data available.", inline=False)

    embed.set_footer(text=f"Source: {status.source_url}")
    return embed


def _build_error_embed(message: str) -> discord.Embed:
    return discord.Embed(title="Unciv Request Failed", description=message, color=discord.Color.red())


def _build_health_embed(health: ServerHealthStatus) -> discord.Embed:
    embed = discord.Embed(title="Unciv Server Health", color=discord.Color.green())
    embed.add_field(name="Server", value=health.base_url, inline=False)
    embed.add_field(name="Detected API", value=health.api_version, inline=True)
    embed.add_field(name="Checked Endpoint", value=health.endpoint, inline=False)

    if health.auth_version is not None:
        embed.add_field(name="authVersion", value=str(health.auth_version), inline=True)
    if health.chat_version is not None:
        embed.add_field(name="chatVersion", value=str(health.chat_version), inline=True)
    if health.version is not None:
        embed.add_field(name="version", value=str(health.version), inline=True)

    return embed


def _build_tracking_list_embed(entries: list[TrackedGame]) -> discord.Embed:
    embed = discord.Embed(title="Tracked Unciv Games", color=discord.Color.teal())
    if not entries:
        embed.description = "No tracked games in this channel."
        return embed

    lines: list[str] = []
    for index, entry in enumerate(entries, start=1):
        state = "unknown" if entry.last_state is None else entry.last_state
        alias = f" ({entry.alias})" if entry.alias else ""
        lines.append(f"{index}. {entry.game_id}{alias} | state={state}")

    embed.description = "\n".join(lines)
    return embed


def _build_turn_change_embed(
    tracked: TrackedGame,
    status: UncivGameStatus,
    previous_turn: int | None,
    previous_player: str | None,
) -> discord.Embed:
    embed = _build_status_embed(status)
    embed.title = f"Turn Update: {tracked.display_name}"
    embed.color = discord.Color.gold()

    before_turn = "Unknown" if previous_turn is None else str(previous_turn)
    before_player = previous_player or "Unknown"
    after_turn = "Unknown" if status.turn is None else str(status.turn)
    after_player = status.current_player or "Unknown"

    embed.add_field(
        name="Change",
        value=f"Turn {before_turn} ({before_player}) -> Turn {after_turn} ({after_player})",
        inline=False,
    )
    return embed


def _read_timeout() -> int:
    raw = os.getenv("UNCIV_REQUEST_TIMEOUT", "15")
    try:
        timeout = int(raw)
    except ValueError:
        return 15
    return min(max(timeout, 3), 60)


def _read_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default

    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _read_track_poll_interval() -> int:
    raw = os.getenv("UNCIV_TRACK_POLL_INTERVAL_SEC", "90")
    try:
        interval = int(raw)
    except ValueError:
        return 90
    return min(max(interval, TRACK_INTERVAL_MIN_SECONDS), TRACK_INTERVAL_MAX_SECONDS)


def _tracked_games_path() -> Path:
    raw = (os.getenv("UNCIV_TRACK_FILE") or DEFAULT_TRACK_FILE).strip()
    return Path(raw)


def _normalize_game_id(game_id: str) -> str:
    cleaned = game_id.strip()
    if not cleaned:
        raise ValueError("Game ID cannot be empty")
    return cleaned


def _tracked_key(channel_id: int, game_id: str) -> str:
    return f"{channel_id}:{game_id.lower()}"


def _status_state_key(status: UncivGameStatus) -> str:
    return f"{status.turn}|{(status.current_player or '').strip().lower()}"


def _tracked_to_dict(entry: TrackedGame) -> dict[str, Any]:
    return {
        "channel_id": entry.channel_id,
        "game_id": entry.game_id,
        "alias": entry.alias,
        "last_state": entry.last_state,
        "last_turn": entry.last_turn,
        "last_current_player": entry.last_current_player,
    }


def _tracked_from_dict(data: dict[str, Any]) -> TrackedGame | None:
    channel_id = _to_int(data.get("channel_id"))
    game_id = _to_text(data.get("game_id"))
    if channel_id is None or channel_id <= 0 or not game_id:
        return None

    return TrackedGame(
        channel_id=channel_id,
        game_id=game_id,
        alias=_to_text(data.get("alias")),
        last_state=_to_text(data.get("last_state")),
        last_turn=_to_int(data.get("last_turn")),
        last_current_player=_to_text(data.get("last_current_player")),
    )


def _load_tracked_games(file_path: Path) -> dict[str, TrackedGame]:
    if not file_path.exists():
        return {}

    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

    if not isinstance(payload, list):
        return {}

    tracked: dict[str, TrackedGame] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        parsed = _tracked_from_dict(item)
        if parsed is None:
            continue
        tracked[_tracked_key(parsed.channel_id, parsed.game_id)] = parsed

    return tracked


def _save_tracked_games(file_path: Path, tracked: dict[str, TrackedGame]) -> None:
    payload = [_tracked_to_dict(entry) for entry in tracked.values()]
    if file_path.parent != Path("."):
        file_path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    temp_path.replace(file_path)


def create_bot() -> commands.Bot:
    intents = discord.Intents.default()
    enable_message_content_intent = _read_bool_env("ENABLE_MESSAGE_CONTENT_INTENT", False)
    intents.message_content = enable_message_content_intent

    command_prefix = "!" if enable_message_content_intent else commands.when_mentioned
    bot = commands.Bot(command_prefix=command_prefix, intents=intents, help_command=None)

    base_url = os.getenv("UNCIV_SERVER_BASE_URL", "https://uncivserver.xyz")
    url_template = os.getenv("UNCIV_GAME_URL_TEMPLATE", "")
    timeout_seconds = _read_timeout()
    track_poll_interval = _read_track_poll_interval()
    track_file = _tracked_games_path()

    has_synced_tree = False
    poll_task: asyncio.Task[None] | None = None

    tracked_games = _load_tracked_games(track_file)

    unciv_client = UncivClient(
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        url_template=url_template,
    )

    def _persist_tracked_games() -> None:
        try:
            _save_tracked_games(track_file, tracked_games)
        except OSError as exc:
            print(f"Failed to save tracked games to {track_file}: {exc}")

    def _channel_entries(channel_id: int) -> list[TrackedGame]:
        entries = [entry for entry in tracked_games.values() if entry.channel_id == channel_id]
        return sorted(entries, key=lambda entry: (entry.alias or entry.game_id).lower())

    async def _get_status(game_id: str) -> UncivGameStatus:
        return await asyncio.to_thread(unciv_client.fetch_game_status, game_id)

    async def _get_health() -> ServerHealthStatus:
        return await asyncio.to_thread(unciv_client.probe_server)

    async def _poll_tracked_games_once() -> None:
        if not tracked_games:
            return

        dirty = False
        for key, tracked in list(tracked_games.items()):
            try:
                status = await _get_status(tracked.game_id)
            except UncivAPIError as exc:
                print(f"[track] Failed to poll {tracked.display_name}: {exc}")
                continue
            except Exception as exc:  # pragma: no cover
                print(f"[track] Unexpected poll error for {tracked.display_name}: {exc}")
                continue

            state_key = _status_state_key(status)
            if tracked.last_state is None:
                tracked.last_state = state_key
                tracked.last_turn = status.turn
                tracked.last_current_player = status.current_player
                dirty = True
                continue

            if state_key == tracked.last_state:
                continue

            previous_turn = tracked.last_turn
            previous_player = tracked.last_current_player
            tracked.last_state = state_key
            tracked.last_turn = status.turn
            tracked.last_current_player = status.current_player
            dirty = True

            channel = bot.get_channel(tracked.channel_id)
            if channel is None:
                try:
                    channel = await bot.fetch_channel(tracked.channel_id)
                except discord.HTTPException:
                    print(f"[track] Channel {tracked.channel_id} is not available. Removing {tracked.game_id}.")
                    tracked_games.pop(key, None)
                    dirty = True
                    continue

            if not hasattr(channel, "send"):
                continue

            try:
                await channel.send(  # type: ignore[union-attr]
                    embed=_build_turn_change_embed(tracked, status, previous_turn, previous_player)
                )
            except discord.HTTPException as exc:
                print(f"[track] Failed to send turn alert for {tracked.display_name}: {exc}")

        if dirty:
            _persist_tracked_games()

    async def _tracked_poll_loop() -> None:
        await bot.wait_until_ready()
        while not bot.is_closed():
            try:
                await _poll_tracked_games_once()
            except Exception as exc:  # pragma: no cover
                print(f"[track] Poll loop error: {exc}")
            await asyncio.sleep(track_poll_interval)

    @bot.event
    async def on_ready() -> None:
        nonlocal has_synced_tree, poll_task
        if not has_synced_tree:
            try:
                synced = await bot.tree.sync()
                print(f"Synced {len(synced)} slash command(s).")
            except discord.HTTPException as exc:
                print(f"Failed to sync slash commands: {exc}")
            has_synced_tree = True

        if poll_task is None or poll_task.done():
            poll_task = asyncio.create_task(_tracked_poll_loop())

        print(f"Logged in as {bot.user} (id={bot.user.id if bot.user else 'unknown'})")
        print(
            f"Tracking {len(tracked_games)} game(s); poll interval={track_poll_interval}s; track file={track_file}"
        )
        if not enable_message_content_intent:
            print(
                "Message Content intent is disabled. Use slash commands: "
                "/ping, /unciv game, /unciv health, /unciv track ..."
            )

    @bot.command(name="ping")
    async def ping(ctx: commands.Context) -> None:
        latency_ms = round(bot.latency * 1000)
        await ctx.send(f"Pong! {latency_ms} ms")

    @bot.group(name="unciv", invoke_without_command=True)
    async def unciv(ctx: commands.Context) -> None:
        await ctx.send("Usage: !unciv game <game_id> | !unciv health | !unciv track <add|remove|list>")

    @unciv.command(name="game", aliases=["status"])
    async def unciv_game(ctx: commands.Context, game_id: str) -> None:
        async with ctx.typing():
            try:
                status = await _get_status(_normalize_game_id(game_id))
            except (UncivAPIError, ValueError) as exc:
                await ctx.send(embed=_build_error_embed(str(exc)))
                return
            except Exception as exc:  # pragma: no cover
                await ctx.send(embed=_build_error_embed(f"Unexpected error: {exc}"))
                return

        await ctx.send(embed=_build_status_embed(status))

    @unciv.command(name="health")
    async def unciv_health(ctx: commands.Context) -> None:
        async with ctx.typing():
            try:
                health = await _get_health()
            except UncivAPIError as exc:
                await ctx.send(embed=_build_error_embed(str(exc)))
                return
            except Exception as exc:  # pragma: no cover
                await ctx.send(embed=_build_error_embed(f"Unexpected error: {exc}"))
                return

        await ctx.send(embed=_build_health_embed(health))

    @unciv.group(name="track", invoke_without_command=True)
    async def unciv_track(ctx: commands.Context) -> None:
        await ctx.send("Usage: !unciv track add <game_id> [alias] | remove <game_id> | list")

    @unciv_track.command(name="add")
    async def unciv_track_add(ctx: commands.Context, game_id: str, *, alias: str = "") -> None:
        channel_id = ctx.channel.id
        try:
            cleaned_game_id = _normalize_game_id(game_id)
        except ValueError as exc:
            await ctx.send(embed=_build_error_embed(str(exc)))
            return

        async with ctx.typing():
            try:
                status = await _get_status(cleaned_game_id)
            except UncivAPIError as exc:
                await ctx.send(embed=_build_error_embed(str(exc)))
                return
            except Exception as exc:  # pragma: no cover
                await ctx.send(embed=_build_error_embed(f"Unexpected error: {exc}"))
                return

        key = _tracked_key(channel_id, cleaned_game_id)
        is_new = key not in tracked_games
        tracked_games[key] = TrackedGame(
            channel_id=channel_id,
            game_id=cleaned_game_id,
            alias=_to_text(alias),
            last_state=_status_state_key(status),
            last_turn=status.turn,
            last_current_player=status.current_player,
        )
        _persist_tracked_games()

        verb = "Started" if is_new else "Updated"
        await ctx.send(f"{verb} tracking for {tracked_games[key].display_name} in this channel.")

    @unciv_track.command(name="remove")
    async def unciv_track_remove(ctx: commands.Context, game_id: str) -> None:
        channel_id = ctx.channel.id
        try:
            cleaned_game_id = _normalize_game_id(game_id)
        except ValueError as exc:
            await ctx.send(embed=_build_error_embed(str(exc)))
            return

        key = _tracked_key(channel_id, cleaned_game_id)
        removed = tracked_games.pop(key, None)
        if removed is None:
            await ctx.send(f"No tracked game found for {cleaned_game_id} in this channel.")
            return

        _persist_tracked_games()
        await ctx.send(f"Stopped tracking {removed.display_name} in this channel.")

    @unciv_track.command(name="list")
    async def unciv_track_list(ctx: commands.Context) -> None:
        entries = _channel_entries(ctx.channel.id)
        await ctx.send(embed=_build_tracking_list_embed(entries))

    unciv_group = app_commands.Group(name="unciv", description="Unciv multiplayer commands")
    track_group = app_commands.Group(name="track", description="Track games and receive turn alerts")

    @bot.tree.command(name="ping", description="Basic bot health check")
    async def slash_ping(interaction: discord.Interaction) -> None:
        latency_ms = round(bot.latency * 1000)
        await interaction.response.send_message(f"Pong! {latency_ms} ms")

    @unciv_group.command(name="game", description="Show status for a game ID")
    @app_commands.describe(game_id="Unciv multiplayer game ID")
    async def slash_unciv_game(interaction: discord.Interaction, game_id: str) -> None:
        await interaction.response.defer(thinking=True)
        try:
            status = await _get_status(_normalize_game_id(game_id))
        except (UncivAPIError, ValueError) as exc:
            await interaction.followup.send(embed=_build_error_embed(str(exc)))
            return
        except Exception as exc:  # pragma: no cover
            await interaction.followup.send(embed=_build_error_embed(f"Unexpected error: {exc}"))
            return

        await interaction.followup.send(embed=_build_status_embed(status))

    @unciv_group.command(name="status", description="Alias of /unciv game")
    @app_commands.describe(game_id="Unciv multiplayer game ID")
    async def slash_unciv_status(interaction: discord.Interaction, game_id: str) -> None:
        await slash_unciv_game(interaction, game_id)

    @unciv_group.command(name="health", description="Check server reachability and API version")
    async def slash_unciv_health(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        try:
            health = await _get_health()
        except UncivAPIError as exc:
            await interaction.followup.send(embed=_build_error_embed(str(exc)))
            return
        except Exception as exc:  # pragma: no cover
            await interaction.followup.send(embed=_build_error_embed(f"Unexpected error: {exc}"))
            return

        await interaction.followup.send(embed=_build_health_embed(health))

    @track_group.command(name="add", description="Track a game in this channel")
    @app_commands.describe(game_id="Unciv multiplayer game ID", alias="Optional display name")
    async def slash_track_add(
        interaction: discord.Interaction,
        game_id: str,
        alias: str | None = None,
    ) -> None:
        await interaction.response.defer(thinking=True)

        channel_id = interaction.channel_id
        if channel_id is None:
            await interaction.followup.send(embed=_build_error_embed("Cannot determine channel for tracking."))
            return

        try:
            cleaned_game_id = _normalize_game_id(game_id)
        except ValueError as exc:
            await interaction.followup.send(embed=_build_error_embed(str(exc)))
            return

        try:
            status = await _get_status(cleaned_game_id)
        except UncivAPIError as exc:
            await interaction.followup.send(embed=_build_error_embed(str(exc)))
            return
        except Exception as exc:  # pragma: no cover
            await interaction.followup.send(embed=_build_error_embed(f"Unexpected error: {exc}"))
            return

        key = _tracked_key(channel_id, cleaned_game_id)
        is_new = key not in tracked_games
        tracked_games[key] = TrackedGame(
            channel_id=channel_id,
            game_id=cleaned_game_id,
            alias=_to_text(alias),
            last_state=_status_state_key(status),
            last_turn=status.turn,
            last_current_player=status.current_player,
        )
        _persist_tracked_games()

        verb = "Started" if is_new else "Updated"
        await interaction.followup.send(f"{verb} tracking for {tracked_games[key].display_name} in this channel.")

    @track_group.command(name="remove", description="Stop tracking a game in this channel")
    @app_commands.describe(game_id="Unciv multiplayer game ID")
    async def slash_track_remove(interaction: discord.Interaction, game_id: str) -> None:
        channel_id = interaction.channel_id
        if channel_id is None:
            await interaction.response.send_message(
                embed=_build_error_embed("Cannot determine channel for tracking."),
                ephemeral=True,
            )
            return

        try:
            cleaned_game_id = _normalize_game_id(game_id)
        except ValueError as exc:
            await interaction.response.send_message(embed=_build_error_embed(str(exc)), ephemeral=True)
            return

        key = _tracked_key(channel_id, cleaned_game_id)
        removed = tracked_games.pop(key, None)
        if removed is None:
            await interaction.response.send_message(
                f"No tracked game found for {cleaned_game_id} in this channel.",
                ephemeral=True,
            )
            return

        _persist_tracked_games()
        await interaction.response.send_message(f"Stopped tracking {removed.display_name} in this channel.")

    @track_group.command(name="list", description="List tracked games in this channel")
    async def slash_track_list(interaction: discord.Interaction) -> None:
        channel_id = interaction.channel_id
        if channel_id is None:
            await interaction.response.send_message(
                embed=_build_error_embed("Cannot determine channel for tracking."),
                ephemeral=True,
            )
            return

        entries = _channel_entries(channel_id)
        await interaction.response.send_message(embed=_build_tracking_list_embed(entries))

    unciv_group.add_command(track_group)
    bot.tree.add_command(unciv_group)

    @bot.command(name="help")
    async def help_command(ctx: commands.Context) -> None:
        await ctx.send(
            "Commands:\n"
            "!ping\n"
            "!unciv game <game_id>\n"
            "!unciv health\n"
            "!unciv track add <game_id> [alias]\n"
            "!unciv track remove <game_id>\n"
            "!unciv track list\n"
            "/ping\n"
            "/unciv game <game_id>\n"
            "/unciv status <game_id>\n"
            "/unciv health\n"
            "/unciv track add <game_id> [alias]\n"
            "/unciv track remove <game_id>\n"
            "/unciv track list"
        )

    @bot.event
    async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("Missing required argument. Use !help to see command usage.")
            return
        raise error

    return bot


def main() -> None:
    token = (os.getenv("DISCORD_BOT_TOKEN") or "").strip()
    if not token or token == "YOUR_DISCORD_BOT_TOKEN_HERE":
        raise RuntimeError("Set DISCORD_BOT_TOKEN in .env before running the bot.")

    bot = create_bot()
    bot.run(token)


if __name__ == "__main__":
    main()

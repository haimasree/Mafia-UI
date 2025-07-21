"""
Main FastAPI application for the Mafia game web interface.
This replaces the need for two separate terminal windows (player_chat.py and player_input.py)
with a single web interface accessible through a browser.
"""

import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import json
import asyncio
from pathlib import Path
from typing import Dict, List
import uvicorn
import time

import threading
import asyncio
from concurrent.futures import ThreadPoolExecutor

# Import your existing game modules
# Note: You'll need to make sure these imports work with your file structure
try:
    from game_constants import *
    from game_status_checks import (
        is_game_over, is_time_to_vote, all_players_joined,
        get_is_mafia, is_nighttime, is_voted_out
    )
    from player_survey import run_survey_about_llm_player
except ImportError:
    print("Warning: Could not import game modules. Make sure they are in the same directory.")

# Create FastAPI application instance
app = FastAPI(title="Mafia Game Web Interface")

# Set up templates for HTML rendering
templates = Jinja2Templates(directory="templates")

# Mount static files (CSS, JavaScript) - we'll create these
app.mount("/static", StaticFiles(directory="static"), name="static")

# Thread-safe storage for concurrent player access
_data_lock = threading.RLock()  # Reentrant lock for nested access

# Store active WebSocket connections for real-time chat
# Format: {game_id: {player_name: websocket}}
active_connections: Dict[str, Dict[str, WebSocket]] = {}

# Store player sessions
# Format: {session_id: {game_id, player_name, is_mafia, real_name}}
player_sessions: Dict[str, Dict] = {}

# Track votes to prevent multiple votes per round
# Format: {game_id: {player_name: {round_number: [list_of_votes]}}}
player_vote_tracking: Dict[str, Dict[str, Dict[int, List[str]]]] = {}


class ConnectionManager:
    """
    Thread-safe WebSocket connection manager for multiple concurrent players.
    Each player gets their own connection while sharing the same server instance.
    """

    def __init__(self):
        # Store connections by game_id and player_name
        self.active_connections: Dict[str, Dict[str, WebSocket]] = {}
        self._lock = threading.RLock()

    async def connect(self, websocket: WebSocket, game_id: str, player_name: str):
        """Accept a new WebSocket connection and add it to the game room"""
        await websocket.accept()

        with self._lock:
            if game_id not in self.active_connections:
                self.active_connections[game_id] = {}

            self.active_connections[game_id][player_name] = websocket
            print(f"[Thread {threading.current_thread().ident}] Player {player_name} connected to game {game_id}")

    def disconnect(self, game_id: str, player_name: str):
        """Remove a WebSocket connection"""
        with self._lock:
            if game_id in self.active_connections:
                if player_name in self.active_connections[game_id]:
                    del self.active_connections[game_id][player_name]
                    print(f"[Thread {threading.current_thread().ident}] Player {player_name} disconnected from game {game_id}")

    async def send_personal_message(self, message: str, game_id: str, player_name: str):
        """Send a message to a specific player"""
        with self._lock:
            if (game_id in self.active_connections and
                player_name in self.active_connections[game_id]):
                websocket = self.active_connections[game_id][player_name]
                try:
                    await websocket.send_text(message)
                except:
                    # Connection might be closed, remove it
                    self.disconnect(game_id, player_name)

    async def broadcast_to_game(self, message: str, game_id: str, exclude_player: str = None):
        """Send a message to all players in a game (optionally excluding one player)"""
        connections_copy = {}

        with self._lock:
            if game_id in self.active_connections:
                connections_copy = self.active_connections[game_id].copy()

        for player_name, websocket in connections_copy.items():
            if exclude_player and player_name == exclude_player:
                continue
            try:
                await websocket.send_text(message)
            except:
                # Connection might be closed, remove it
                self.disconnect(game_id, player_name)

# Create connection manager instance
manager = ConnectionManager()


def validate_game_exists(game_id: str) -> bool:
    """
    Check if a game directory exists and is valid.
    This integrates with your existing file-based game system.
    """
    try:
        game_dir = Path("games") / game_id  # Adjust path as needed
        return game_dir.exists() and game_dir.is_dir()
    except:
        return False


def get_game_directory(game_id: str) -> Path:
    """Get the Path object for a game directory"""
    return Path("games") / game_id  # Adjust path as needed


def get_available_players(game_id: str) -> List[str]:
    """
    Get list of available player names for the game.
    This reads from your existing PLAYER_NAMES_FILE.
    """
    try:
        game_dir = get_game_directory(game_id)
        player_names_file = game_dir / PLAYER_NAMES_FILE
        if player_names_file.exists():
            return player_names_file.read_text().splitlines()
        return []
    except:
        return []


def assign_character_name(game_id: str, real_name: str) -> str:
    """
    Get the pre-assigned character name for a player based on their real name.
    This reads from the REAL_NAMES_FILE that maps real names to character names.
    """
    try:
        game_dir = get_game_directory(game_id)
        real_names_file = game_dir / REAL_NAMES_FILE

        if not real_names_file.exists():
            return None

        # Extract the mapping as described by the user
        real_names_to_codenames_str = real_names_file.read_text().splitlines()
        real_names_to_codenames = dict([
            real_to_code.split(REAL_NAME_CODENAME_DELIMITER)
            for real_to_code in real_names_to_codenames_str
        ])

        # Return the mapped character name for this real name
        return real_names_to_codenames.get(real_name)

    except Exception as e:
        print(f"Error getting character name: {e}")
        return None


def get_available_real_names(game_id: str) -> List[str]:
    """
    Get list of available real names that can join the game.
    This reads from the REAL_NAMES_FILE to get all pre-registered players.
    """
    try:
        game_dir = get_game_directory(game_id)
        real_names_file = game_dir / REAL_NAMES_FILE

        if not real_names_file.exists():
            return []

        # Extract the mapping
        real_names_to_codenames_str = real_names_file.read_text().splitlines()
        real_names_to_codenames = dict([
            real_to_code.split(REAL_NAME_CODENAME_DELIMITER)
            for real_to_code in real_names_to_codenames_str
        ])

        # Check which players haven't joined yet
        available_real_names = []
        for real_name, character_name in real_names_to_codenames.items():
            status_file = game_dir / PERSONAL_STATUS_FILE_FORMAT.format(character_name)
            if not status_file.exists() or status_file.read_text().strip() != JOINED:
                available_real_names.append(real_name)

        return available_real_names

    except Exception as e:
        print(f"Error getting available real names: {e}")
        return []


def get_remaining_players_for_voting(game_id: str, current_player: str) -> List[str]:
    """
    Get list of players that can be voted for.
    This reads from REMAINING_PLAYERS_FILE and excludes the current player.
    """
    try:
        game_dir = get_game_directory(game_id)
        remaining_file = game_dir / REMAINING_PLAYERS_FILE

        if not remaining_file.exists():
            return []

        remaining_players = remaining_file.read_text().splitlines()
        # Remove current player (players can't vote for themselves)
        return [player for player in remaining_players if player != current_player]

    except Exception as e:
        print(f"Error getting remaining players: {e}")
        return []


def get_current_round(game_id: str) -> int:
    """
    Calculate the current round number based on remaining players.
    """
    try:
        game_dir = get_game_directory(game_id)
        total_players = len((game_dir / PLAYER_NAMES_FILE).read_text().splitlines())
        remaining_players = len((game_dir / REMAINING_PLAYERS_FILE).read_text().splitlines())
        return total_players - remaining_players + 1
    except:
        return 1

def safe_get_session(session_id: str) -> Dict:
    """Thread-safe session retrieval"""
    with _data_lock:
        return player_sessions.get(session_id, {}).copy()

def safe_set_session(session_id: str, session_data: Dict):
    """Thread-safe session storage"""
    with _data_lock:
        player_sessions[session_id] = session_data.copy()

def safe_has_player_voted(game_id: str, character_name: str, current_round: int) -> bool:
    """Thread-safe vote checking"""
    with _data_lock:
        if game_id not in player_vote_tracking:
            return False

        if character_name not in player_vote_tracking[game_id]:
            return False

        if current_round not in player_vote_tracking[game_id][character_name]:
            return False

        return len(player_vote_tracking[game_id][character_name][current_round]) > 0

def safe_record_vote(game_id: str, character_name: str, current_round: int, voted_player: str):
    """Thread-safe vote recording"""
    with _data_lock:
        if game_id not in player_vote_tracking:
            player_vote_tracking[game_id] = {}

        if character_name not in player_vote_tracking[game_id]:
            player_vote_tracking[game_id][character_name] = {}

        if current_round not in player_vote_tracking[game_id][character_name]:
            player_vote_tracking[game_id][character_name][current_round] = []

        player_vote_tracking[game_id][character_name][current_round].append(voted_player)

def safe_get_player_vote(game_id: str, character_name: str, current_round: int) -> str:
    """Thread-safe vote retrieval"""
    with _data_lock:
        if (game_id in player_vote_tracking and
            character_name in player_vote_tracking[game_id] and
            current_round in player_vote_tracking[game_id][character_name] and
            len(player_vote_tracking[game_id][character_name][current_round]) > 0):
            return player_vote_tracking[game_id][character_name][current_round][0]
        return None


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """
    Home page where users enter their game ID.
    This replaces the command line argument for game_id.
    """
    return templates.TemplateResponse("home.html", {"request": request})


@app.post("/join-game")
async def join_game(request: Request, game_id: str = Form(...)):
    """
    Handle game ID submission and redirect to name selection.
    """
    if not validate_game_exists(game_id):
        return templates.TemplateResponse(
            "home.html",
            {"request": request, "error": f"Game {game_id} does not exist!"}
        )

    # Redirect to name selection page
    return RedirectResponse(url=f"/select-name/{game_id}", status_code=303)


@app.get("/select-name/{game_id}", response_class=HTMLResponse)
async def select_name_page(request: Request, game_id: str):
    """
    Page where users select their real name from available options.
    This shows only real names that haven't joined the game yet.
    """
    if not validate_game_exists(game_id):
        raise HTTPException(status_code=404, detail="Game not found")

    available_names = get_available_real_names(game_id)

    return templates.TemplateResponse(
        "select_name.html",
        {
            "request": request,
            "game_id": game_id,
            "available_names": available_names
        }
    )


@app.post("/assign-character/{game_id}")
async def assign_character(request: Request, game_id: str, real_name: str = Form(...)):
    """
    Assign a character name to the player and redirect to the game interface.
    Each player gets their own session in a thread-safe manner.
    """
    if not validate_game_exists(game_id):
        raise HTTPException(status_code=404, detail="Game not found")

    if not real_name.strip():
        return templates.TemplateResponse(
            "select_name.html",
            {"request": request, "game_id": game_id, "error": "Please enter your real name"}
        )

    # Assign character name
    character_name = assign_character_name(game_id, real_name.strip())

    if not character_name:
        return templates.TemplateResponse(
            "select_name.html",
            {"request": request, "game_id": game_id, "error": "No available characters in this game"}
        )

    # Create unique session for this player thread
    import uuid
    session_id = str(uuid.uuid4())

    # Get player role (mafia or not)
    game_dir = get_game_directory(game_id)
    is_mafia = get_is_mafia(character_name, game_dir)

    # Store session information in thread-safe manner
    session_data = {
        "game_id": game_id,
        "character_name": character_name,
        "real_name": real_name.strip(),
        "is_mafia": is_mafia,
        "thread_id": threading.current_thread().ident
    }
    safe_set_session(session_id, session_data)

    # Mark player as joined (integrating with existing game logic)
    try:
        status_file = game_dir / PERSONAL_STATUS_FILE_FORMAT.format(character_name)
        status_file.write_text(JOINED)
        print(f"[Thread {threading.current_thread().ident}] Player {character_name} joined game {game_id}")
    except Exception as e:
        print(f"Error marking player as joined: {e}")

    # Redirect to game interface with session ID
    response = RedirectResponse(url=f"/game/{game_id}", status_code=303)
    response.set_cookie(key="session_id", value=session_id)
    return response


@app.get("/game/{game_id}", response_class=HTMLResponse)
async def game_interface(request: Request, game_id: str):
    """
    Main game interface that combines chat and input functionality.
    This replaces both player_chat.py and player_input.py.
    """
    # Get session information
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in player_sessions:
        return RedirectResponse(url="/")

    session = player_sessions[session_id]
    if session["game_id"] != game_id:
        return RedirectResponse(url="/")
    environment = os.getenv("FASTAPI_ENV", "")   
    if environment == "Docker":
        websocket_url = "ws://localhost"
    elif environment == "Prod":
        websocket_url = "wss://localhost"
    else:
        websocket_url = "ws://localhost:8000"

    return templates.TemplateResponse(
        "game.html",
        {
            "request": request,
            "game_id": game_id,
            "character_name": session["character_name"],
            "real_name": session["real_name"],
            "is_mafia": session["is_mafia"],
            "websocket_url": websocket_url
        }
    )


@app.websocket("/ws/{game_id}")
async def websocket_endpoint(websocket: WebSocket, game_id: str):
    """
    WebSocket endpoint for real-time chat functionality.
    Each player connection runs in its own async task/thread context.
    """
    # Get player information from query parameters
    query_params = dict(websocket.query_params)
    session_id = query_params.get("session_id")

    if not session_id:
        await websocket.close(code=4001)
        return

    session = safe_get_session(session_id)
    if not session:
        await websocket.close(code=4001)
        return

    character_name = session["character_name"]
    is_mafia = session["is_mafia"]
    thread_id = threading.current_thread().ident

    print(f"[Thread {thread_id}] Starting WebSocket for {character_name} in game {game_id}")

    await manager.connect(websocket, game_id, character_name)

    try:
        # Send initial game state
        await send_game_state(websocket, game_id, character_name, is_mafia)

        # Start background task to monitor game files for this player
        monitor_task = asyncio.create_task(
            monitor_game_files(websocket, game_id, character_name, is_mafia)
        )

        # Listen for messages from this player's client
        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)

            # Handle player action in thread-safe manner
            await handle_player_action(message_data, game_id, character_name, is_mafia)

    except WebSocketDisconnect:
        print(f"[Thread {thread_id}] Player {character_name} disconnected from game {game_id}")
        manager.disconnect(game_id, character_name)
        monitor_task.cancel()
    except Exception as e:
        print(f"[Thread {thread_id}] WebSocket error for {character_name}: {e}")
        manager.disconnect(game_id, character_name)
        if 'monitor_task' in locals():
            monitor_task.cancel()


async def send_game_state(websocket: WebSocket, game_id: str, character_name: str, is_mafia: bool):
    """Send current game state to the player"""
    try:
        game_dir = get_game_directory(game_id)

        # Send role information
        role = "Mafia" if is_mafia else "Bystander"
        role_color = "red" if is_mafia else "blue"

        await websocket.send_text(json.dumps({
            "type": "role_info",
            "role": role,
            "color": role_color,
            "character_name": character_name
        }))

        # Send existing chat messages
        await send_existing_messages(websocket, game_dir, is_mafia)

    except Exception as e:
        print(f"Error sending game state: {e}")


async def send_existing_messages(websocket: WebSocket, game_dir: Path, is_mafia: bool):
    """Send existing chat messages to the player"""
    try:
        # Send manager messages
        manager_file = game_dir / PUBLIC_MANAGER_CHAT_FILE
        if manager_file.exists():
            messages = manager_file.read_text().splitlines()
            for message in messages:
                await websocket.send_text(json.dumps({
                    "type": "chat_message",
                    "content": message,
                    "color": "green"
                }))

        # Send daytime messages
        daytime_file = game_dir / PUBLIC_DAYTIME_CHAT_FILE
        if daytime_file.exists():
            messages = daytime_file.read_text().splitlines()
            for message in messages:
                await websocket.send_text(json.dumps({
                    "type": "chat_message",
                    "content": message,
                    "color": "blue"
                }))

        # Send nighttime messages (only for mafia)
        if is_mafia:
            nighttime_file = game_dir / PUBLIC_NIGHTTIME_CHAT_FILE
            if nighttime_file.exists():
                messages = nighttime_file.read_text().splitlines()
                for message in messages:
                    await websocket.send_text(json.dumps({
                        "type": "chat_message",
                        "content": message,
                        "color": "red"
                    }))
    except Exception as e:
        print(f"Error sending existing messages: {e}")


async def monitor_game_files(websocket: WebSocket, game_id: str, character_name: str, is_mafia: bool):
    """
    Monitor game files for changes and send updates to the client.
    This replaces the polling mechanism from the original player_chat.py.
    """
    game_dir = get_game_directory(game_id)
    last_manager_lines = 0
    last_daytime_lines = 0
    last_nighttime_lines = 0
    game_started = False
    player_voted_out = False
    last_voting_state = False
    last_round = 0

    while True:
        try:
            # Check if all players have joined and game can start
            if not game_started and all_players_joined(game_dir):
                await websocket.send_text(json.dumps({
                    "type": "game_started",
                    "message": "All players have joined! The game begins!"
                }))
                await websocket.send_text(json.dumps({
                    "type": "update_status",
                    "message": "Game in progress..."
                }))
                game_started = True

            # Check if this player has been voted out
            if not player_voted_out and is_voted_out(character_name, game_dir):
                await websocket.send_text(json.dumps({
                    "type": "voted_out",
                    "message": "You have been eliminated! You can observe but not participate."
                }))
                player_voted_out = True

            # Check if game is over
            if is_game_over(game_dir):
                await websocket.send_text(json.dumps({
                    "type": "game_over",
                    "message": "Game has ended! Time for the survey."
                }))
                break

            # Check for new manager messages
            manager_file = game_dir / PUBLIC_MANAGER_CHAT_FILE
            if manager_file.exists():
                lines = manager_file.read_text().splitlines()
                if len(lines) > last_manager_lines:
                    new_messages = lines[last_manager_lines:]
                    for message in new_messages:
                        await websocket.send_text(json.dumps({
                            "type": "chat_message",
                            "content": message,
                            "color": "green"
                        }))
                    last_manager_lines = len(lines)

            # Check for new daytime messages
            daytime_file = game_dir / PUBLIC_DAYTIME_CHAT_FILE
            if daytime_file.exists():
                lines = daytime_file.read_text().splitlines()
                if len(lines) > last_daytime_lines:
                    new_messages = lines[last_daytime_lines:]
                    for message in new_messages:
                        await websocket.send_text(json.dumps({
                            "type": "chat_message",
                            "content": message,
                            "color": "blue"
                        }))
                    last_daytime_lines = len(lines)

            # Check for new nighttime messages (only for mafia)
            if is_mafia:
                nighttime_file = game_dir / PUBLIC_NIGHTTIME_CHAT_FILE
                if nighttime_file.exists():
                    lines = nighttime_file.read_text().splitlines()
                    if len(lines) > last_nighttime_lines:
                        new_messages = lines[last_nighttime_lines:]
                        for message in new_messages:
                            await websocket.send_text(json.dumps({
                                "type": "chat_message",
                                "content": message,
                                "color": "red"
                            }))
                        last_nighttime_lines = len(lines)

            # Check voting state and round changes
            current_voting_state = is_time_to_vote(game_dir)
            current_round = get_current_round(game_id)

            # Send voting interface when voting starts OR when round changes
            if current_voting_state and not player_voted_out:
                if is_mafia or not is_nighttime(game_dir):
                    # Check if we need to send/update voting interface
                    should_send_voting = False

                    if not last_voting_state:
                        # Voting just started
                        should_send_voting = True
                    elif current_round != last_round:
                        # New round started
                        should_send_voting = True

                    if should_send_voting:
                        # Check if player has already voted this round
                        if not safe_has_player_voted(game_id, character_name, current_round):
                            remaining_players = get_remaining_players_for_voting(game_id, character_name)
                            await websocket.send_text(json.dumps({
                                "type": "vote_request",
                                "vote_options": remaining_players,
                                "round": current_round
                            }))
                        else:
                            # Player already voted, send their vote
                            voted_player = safe_get_player_vote(game_id, character_name, current_round)
                            await websocket.send_text(json.dumps({
                                "type": "already_voted",
                                "message": f"You have already voted for {voted_player} this round.",
                                "voted_player": voted_player,
                                "round": current_round
                            }))

            elif not current_voting_state and last_voting_state:
                # Voting just ended
                await websocket.send_text(json.dumps({
                    "type": "voting_ended",
                    "message": "Voting time has ended."
                }))

            last_voting_state = current_voting_state
            last_round = current_round

            # Wait before checking again
            await asyncio.sleep(1)

        except Exception as e:
            print(f"Error monitoring game files: {e}")
            break


async def handle_player_action(message_data: dict, game_id: str, character_name: str, is_mafia: bool):
    """
    Handle player actions in a thread-safe manner.
    Each player's actions are processed independently.
    """
    try:
        game_dir = get_game_directory(game_id)
        action_type = message_data.get("type")
        thread_id = threading.current_thread().ident

        # Check if player is voted out
        if is_voted_out(character_name, game_dir):
            return  # Voted out players can't perform actions

        if action_type == "chat_message":
            content = message_data.get("content", "").strip()
            if content:
                # Check if player can chat (not during voting, not nighttime for non-mafia)
                if is_time_to_vote(game_dir):
                    return  # Can't chat during voting

                if not is_mafia and is_nighttime(game_dir):
                    return  # Non-mafia can't chat during nighttime

                # Write to personal chat file (thread-safe file operations)
                chat_file = game_dir / PERSONAL_CHAT_FILE_FORMAT.format(character_name)
                with _data_lock:  # Ensure thread-safe file writing
                    with open(chat_file, "a") as f:
                        f.write(format_message(character_name, content))

                print(f"[Thread {thread_id}] {character_name} sent message: {content[:50]}...")

        elif action_type == "vote":
            voted_player = message_data.get("voted_player")
            if voted_player and is_time_to_vote(game_dir):
                # Check if this player can vote (not nighttime for non-mafia)
                if not is_mafia and is_nighttime(game_dir):
                    return  # Non-mafia can't vote during nighttime

                current_round = get_current_round(game_id)

                # Thread-safe vote checking and recording
                if safe_has_player_voted(game_id, character_name, current_round):
                    return  # Already voted this round

                # Verify the voted player is in the remaining players list
                remaining_players = get_remaining_players_for_voting(game_id, character_name)
                if voted_player in remaining_players:
                    # Write vote to personal vote file (thread-safe)
                    vote_file = game_dir / PERSONAL_VOTE_FILE_FORMAT.format(character_name)
                    with _data_lock:
                        with open(vote_file, "a") as f:
                            f.write(f"{voted_player}\n")

                    # Record vote in thread-safe tracking system
                    safe_record_vote(game_id, character_name, current_round, voted_player)

                    print(f"[Thread {thread_id}] {character_name} voted for {voted_player} in round {current_round}")

        elif action_type == "start_survey":
            # Start the survey for this player
            if is_game_over(game_dir):
                # This would integrate with your survey system
                # For now, we'll send a message that survey should start
                pass

    except Exception as e:
        print(f"[Thread {threading.current_thread().ident}] Error handling player action: {e}")


def get_llm_player_name_web(game_dir: Path) -> str:
    """
    Web version of get_llm_player_name from player_survey.py
    """
    try:
        player_names_file = game_dir / PLAYER_NAMES_FILE
        if not player_names_file.exists():
            return None

        for player_name in player_names_file.read_text().splitlines():
            llm_log_file = game_dir / LLM_LOG_FILE_FORMAT.format(player_name)
            if llm_log_file.exists():
                return player_name
        return None
    except Exception as e:
        print(f"Error getting LLM player name: {e}")
        return None


def get_survey_data(game_id: str, character_name: str) -> dict:
    """
    Prepare survey data for the web interface
    """
    try:
        game_dir = get_game_directory(game_id)
        llm_player_name = get_llm_player_name_web(game_dir)

        survey_data = {
            "has_llm": llm_player_name is not None,
            "llm_player_name": llm_player_name,
            "metrics": METRICS_TO_SCORE if llm_player_name else [],
            "all_players": [],
            "other_players": [],
            "score_bounds": {
                "low": DEFAULT_SCORE_LOW_BOUND,
                "high": DEFAULT_SCORE_HIGH_BOUND
            }
        }

        if llm_player_name:
            # Get all players for LLM identification
            player_names_file = game_dir / PLAYER_NAMES_FILE
            if player_names_file.exists():
                all_players = player_names_file.read_text().splitlines()
                survey_data["all_players"] = all_players
                survey_data["other_players"] = [p for p in all_players if p != character_name]

        return survey_data
    except Exception as e:
        print(f"Error preparing survey data: {e}")
        return {
            "has_llm": False,
            "llm_player_name": None,
            "metrics": [],
            "all_players": [],
            "other_players": [],
            "score_bounds": {"low": 1, "high": 10}
        }


def save_survey_response(game_id: str, character_name: str, survey_response: dict):
    """
    Save survey response to file (web version of survey saving logic)
    """
    try:
        game_dir = get_game_directory(game_id)
        survey_file = game_dir / PERSONAL_SURVEY_FILE_FORMAT.format(character_name)

        with open(survey_file, "w") as f:
            # Save LLM identification if applicable
            if "llm_guess" in survey_response:
                llm_player_name = get_llm_player_name_web(game_dir)
                guess_correctness = int(survey_response["llm_guess"] == llm_player_name)
                f.write(f"{LLM_IDENTIFICATION}{METRIC_NAME_AND_SCORE_DELIMITER}{guess_correctness}\n")

            # Save metric scores
            if "metrics" in survey_response:
                for metric, score in survey_response["metrics"].items():
                    f.write(f"{metric}{METRIC_NAME_AND_SCORE_DELIMITER}{score}\n")

            # Save comments
            if "comments" in survey_response:
                f.write(f"{SURVEY_COMMENTS_TITLE}\n{survey_response['comments']}\n")

        return True
    except Exception as e:
        print(f"Error saving survey response: {e}")
        return False


@app.get("/survey/{game_id}", response_class=HTMLResponse)
async def survey_page(request: Request, game_id: str):
    """
    Survey page for post-game feedback - redirect to identification
    """
    return RedirectResponse(url=f"/survey-identify/{game_id}", status_code=303)


@app.post("/submit-survey/{game_id}")
async def submit_survey(request: Request, game_id: str):
    """
    Handle survey submission
    """
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in player_sessions:
        raise HTTPException(status_code=401, detail="Invalid session")

    session = player_sessions[session_id]
    if session["game_id"] != game_id:
        raise HTTPException(status_code=403, detail="Wrong game")

    character_name = session["character_name"]

    # Get form data
    form_data = await request.form()
    survey_response = {}

    # Process LLM identification
    if "llm_guess" in form_data:
        survey_response["llm_guess"] = form_data["llm_guess"]

    # Process metric scores
    survey_response["metrics"] = {}
    for key, value in form_data.items():
        if key.startswith("metric_"):
            metric_name = key.replace("metric_", "")
            survey_response["metrics"][metric_name] = value

    # Process comments
    if "comments" in form_data:
        survey_response["comments"] = form_data["comments"]

    # Save survey response
    success = save_survey_response(game_id, character_name, survey_response)

    if success:
        return templates.TemplateResponse(
            "survey_complete.html",
            {"request": request, "game_id": game_id}
        )
    else:
        raise HTTPException(status_code=500, detail="Failed to save survey")


@app.post("/start-survey/{game_id}")
async def start_survey(request: Request, game_id: str):
    """
    Redirect to survey page instead of running terminal survey
    """
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in player_sessions:
        raise HTTPException(status_code=401, detail="Invalid session")

    return RedirectResponse(url=f"/survey/{game_id}", status_code=303)

@app.get("/survey-identify/{game_id}", response_class=HTMLResponse)
async def survey_identify_page(request: Request, game_id: str):
    """
    First part of survey - LLM identification
    """
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in player_sessions:
        return RedirectResponse(url="/")

    session = player_sessions[session_id]
    if session["game_id"] != game_id:
        return RedirectResponse(url="/")

    character_name = session["character_name"]
    game_dir = get_game_directory(game_id)

    if not is_game_over(game_dir):
        return RedirectResponse(url=f"/game/{game_id}")

    survey_data = get_survey_data(game_id, character_name)

    return templates.TemplateResponse(
        "survey_identify.html",
        {
            "request": request,
            "game_id": game_id,
            "character_name": character_name,
            "survey_data": survey_data
        }
    )

@app.post("/submit-identification/{game_id}")
async def submit_identification(request: Request, game_id: str, llm_guess: str = Form(...)):
    """
    Handle LLM identification submission and redirect to metrics
    """
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in player_sessions:
        raise HTTPException(status_code=401, detail="Invalid session")

    session = player_sessions[session_id]
    if session["game_id"] != game_id:
        raise HTTPException(status_code=403, detail="Wrong game")

    # Store the guess in session for the next page
    session["llm_guess"] = llm_guess

    return RedirectResponse(url=f"/survey-metrics/{game_id}", status_code=303)

@app.get("/survey-metrics/{game_id}", response_class=HTMLResponse)
async def survey_metrics_page(request: Request, game_id: str):
    """
    Second part of survey - show results and collect metrics
    """
    session_id = request.cookies.get("session_id")
    if not session_id or session_id not in player_sessions:
        return RedirectResponse(url="/")

    session = player_sessions[session_id]
    if session["game_id"] != game_id:
        return RedirectResponse(url="/")

    character_name = session["character_name"]
    game_dir = get_game_directory(game_id)

    if not is_game_over(game_dir):
        return RedirectResponse(url=f"/game/{game_id}")

    survey_data = get_survey_data(game_id, character_name)
    llm_guess = session.get("llm_guess")

    # Check if guess was correct
    correct_guess = llm_guess == survey_data["llm_player_name"]

    return templates.TemplateResponse(
        "survey_metrics.html",
        {
            "request": request,
            "game_id": game_id,
            "character_name": character_name,
            "survey_data": survey_data,
            "llm_guess": llm_guess,
            "correct_guess": correct_guess
        }
    )

if __name__ == "__main__":
    print("=" * 60)
    print("🎭 MAFIA GAME WEB SERVER - MULTI-PLAYER SUPPORT")
    print("=" * 60)
    print("✅ Server runs ONCE and handles multiple concurrent players")
    print("✅ Each player gets their own secure session and thread")
    print("✅ Multiple games can run simultaneously")
    print("✅ Thread-safe operations for all player interactions")
    print()
    print("🔗 Connection Instructions:")
    print("   Players connect via SSH with port forwarding:")
    print("   ssh -L 8000:localhost:8000 username@your-server.com")
    print("   Then open http://localhost:8000 in their browser")
    print()
    print("🚀 Starting server on http://127.0.0.1:8000")
    print("   Press Ctrl+C to stop the server")
    print("=" * 60)

    try:
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=8000,
            log_level="info",
            access_log=True
        )
    except KeyboardInterrupt:
        print("\n🛑 Server stopped by user")
    except Exception as e:
        print(f"\n❌ Server error: {e}")

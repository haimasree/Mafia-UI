import json
import os
from game_constants import *  # incl. argparse, time, Path (from pathlib), colored (from termcolor)


# global variable for the game dir
game_dir = Path()  # will be updated only if __name__ == __main__ (prevents new ones in imports)


class Player:

    def __init__(self, name, is_mafia, **kwargs):
        self.name = name
        self.is_mafia = is_mafia
        self.personal_chat_file = game_dir / PERSONAL_CHAT_FILE_FORMAT.format(self.name)
        self.personal_chat_file_lines_read = 0
        self.personal_vote_file = game_dir / PERSONAL_VOTE_FILE_FORMAT.format(self.name)
        self.personal_vote_file_lines_read = 0
        # status is whether the player has joined and then whether was voted out
        self.personal_status_file = game_dir / PERSONAL_STATUS_FILE_FORMAT.format(self.name)

    def get_new_messages(self):
        with open(self.personal_chat_file, "r") as f:
            # the readlines method includes the "\n"
            lines = f.readlines()[self.personal_chat_file_lines_read:]
        self.personal_chat_file_lines_read += len(lines)
        return lines

    def get_voted_player(self):
        all_votes = self.personal_vote_file.read_text().splitlines()
        new_votes = all_votes[self.personal_vote_file_lines_read:]
        if new_votes:
            self.personal_vote_file_lines_read += len(new_votes)  # should be 1 if works correctly
            return new_votes[-1].strip()
        else:
            return None

    def eliminate(self):
        self.personal_status_file.write_text(VOTED_OUT)


def get_config():
    with open(game_dir / GAME_CONFIG_FILE, "r") as f:
        config = json.load(f)
    return config


def get_players(config):
    return [Player(**player_config) for player_config in config[PLAYERS_KEY_IN_CONFIG]]


def is_win_by_bystanders(mafia_players):
    if len(mafia_players) == 0:
        (game_dir / WHO_WINS_FILE).write_text(BYSTANDERS_WIN_MESSAGE)
        return True
    return False


def is_win_by_mafia(mafia_players, bystanders):
    if len(mafia_players) >= len(bystanders):
        (game_dir / WHO_WINS_FILE).write_text(MAFIA_WINS_MESSAGE)
        return True
    return False


def is_game_over(players):
    mafia_players = [player for player in players if player.is_mafia]
    bystanders = [player for player in players if not player.is_mafia]
    return is_win_by_bystanders(mafia_players) or is_win_by_mafia(mafia_players, bystanders)


def run_chat_round_between_players(players, chat_room):
    for player in players:
        lines = player.get_new_messages()
        with open(chat_room, "a") as f:
            f.writelines(lines)  # lines already include "\n"


def notify_players_about_voting_time(phase_name, public_chat_file):
    phase_end_message = DAYTIME_VOTING_TIME_MESSAGE if phase_name == DAYTIME else NIGHTTIME_VOTING_TIME_MESSAGE
    with open(public_chat_file, "a") as f:  # only to the current phase's active players chat room
        f.write(format_message(GAME_MANAGER_NAME, phase_end_message))
    voting_phase_name = DAYTIME_VOTING_TIME if phase_name == DAYTIME else NIGHTTIME_VOTING_TIME
    (game_dir / PHASE_STATUS_FILE).write_text(voting_phase_name)


def get_voted_out_name(optional_votes_players, public_chat_file, voting_players):
    votes = {player.name: 0 for player in optional_votes_players}
    while voting_players:
        voted_players = []
        for player in voting_players:
            voted_for = player.get_voted_player()
            if not voted_for:
                continue
            voted_players.append(player)
            if voted_for in votes:
                with open(public_chat_file, "a") as f:
                    voting_message = VOTING_MESSAGE_FORMAT.format(player.name, voted_for)
                    f.write(format_message(GAME_MANAGER_NAME, voting_message))
                votes[voted_for] += 1
        for player in voted_players:
            voting_players.remove(player)
    # if there were invalid votes or if there was a tie, decision will be made "randomly"
    voted_out_name = max(votes, key=votes.get)
    return voted_out_name


def voting_sub_phase(phase_name, voting_players, optional_votes_players, public_chat_file, players):
    notify_players_about_voting_time(phase_name, public_chat_file)
    voted_out_name = get_voted_out_name(optional_votes_players, public_chat_file, voting_players[:])
    # update info file of remaining players
    remaining_players = (game_dir / REMAINING_PLAYERS_FILE).read_text().splitlines()
    try:
        remaining_players.remove(voted_out_name)
    except ValueError:
        print(f"BUG: voted_out_name is {voted_out_name}, which is not in remaining_players")
    (game_dir / REMAINING_PLAYERS_FILE).write_text("\n".join(remaining_players))
    # update player object status
    voted_out_player = {player.name: player for player in optional_votes_players}[voted_out_name]
    voted_out_player.eliminate()
    players.remove(voted_out_player)
    announce_voted_out_player(voted_out_player)


def game_manager_announcement(message):
    with open(game_dir / PUBLIC_MANAGER_CHAT_FILE, "a") as f:
        f.write(format_message(GAME_MANAGER_NAME, message))


def announce_voted_out_player(voted_out_player):
    role = get_role_string(voted_out_player.is_mafia)
    voted_out_message = VOTED_OUT_MESSAGE_FORMAT.format(voted_out_player.name, role)
    game_manager_announcement(voted_out_message)


def run_phase(players, voting_players, optional_votes_players, public_chat_file,
              time_limit_seconds, phase_name):
    if len(voting_players) > 1:
        start_time = time.time()
        while time.time() - start_time < time_limit_seconds:
            run_chat_round_between_players(voting_players, public_chat_file)
    else:
        game_manager_announcement(CUTTING_TO_VOTE_MESSAGE)
    print("Now voting starts...")
    voting_sub_phase(phase_name, voting_players, optional_votes_players, public_chat_file, players)


def run_nighttime(players, nighttime_minutes):
    (game_dir / PHASE_STATUS_FILE).write_text(NIGHTTIME)
    mafia_players = [player for player in players if player.is_mafia]
    bystanders = [player for player in players if not player.is_mafia]
    print(colored(NIGHTTIME_START_MESSAGE_FORMAT.format(nighttime_minutes), NIGHTTIME_COLOR))
    game_manager_announcement(NIGHTTIME_START_MESSAGE_FORMAT.format(nighttime_minutes))
    run_phase(players, mafia_players, bystanders, game_dir / PUBLIC_NIGHTTIME_CHAT_FILE,
              minutes_to_seconds(nighttime_minutes), NIGHTTIME)


def run_daytime(players, daytime_minutes):
    (game_dir / PHASE_STATUS_FILE).write_text(DAYTIME)
    print(colored(DAYTIME_START_MESSAGE_FORMAT.format(daytime_minutes), DAYTIME_COLOR))
    game_manager_announcement(DAYTIME_START_MESSAGE_FORMAT.format(daytime_minutes))
    run_phase(players, players, players, game_dir / PUBLIC_DAYTIME_CHAT_FILE,
              minutes_to_seconds(daytime_minutes), DAYTIME)


def wait_for_players(players):
    havent_joined_yet = [player for player in players]
    print("Waiting for all players to connect and start running their programs to join:")
    print(",  ".join([player.name for player in havent_joined_yet]))
    while havent_joined_yet:
        joined = []
        for player in havent_joined_yet:
            if bool(player.personal_status_file.read_text()):  # file isn't empty once joined
                joined.append(player)
                print(f"{player.name} has joined!")
        for player in joined:
            havent_joined_yet.remove(player)
    (game_dir / GAME_START_TIME_FILE).write_text(get_current_timestamp())
    print("Game is now running! Its content is displayed to players.")


def get_all_player_out_of_voting_time():
    current_phase = (game_dir / PHASE_STATUS_FILE).read_text()
    (game_dir / PHASE_STATUS_FILE).write_text(current_phase.replace(VOTING_TIME, ""))


def end_game():
    get_all_player_out_of_voting_time()
    print("Game has finished.")


def main():
    global game_dir
    game_dir = get_game_dir_from_argv()
    config = get_config()
    players = get_players(config)
    wait_for_players(players)
    while not is_game_over(players):
        run_daytime(players, config[DAYTIME_MINUTES_KEY])
        if is_game_over(players):
            break
        run_nighttime(players, config[NIGHTTIME_MINUTES_KEY])
    end_game()


if __name__ == '__main__':
    main()

from llm_players.llm_constants import LLM_CONFIG_KEY, ASYNC_TYPE_KEY
from llm_players.schedule_then_generate_player import ScheduleThenGeneratePlayer
from llm_players.generate_then_schedule_player import GenerateThenSchedulePlayer
from llm_players.fine_tuned_player import FineTunedPlayer
from llm_players.every_x_messages_player import EveryXMessagesPlayer


llm_players_classes = {
    ScheduleThenGeneratePlayer.TYPE_NAME: ScheduleThenGeneratePlayer,
    GenerateThenSchedulePlayer.TYPE_NAME: GenerateThenSchedulePlayer,
    FineTunedPlayer.TYPE_NAME: FineTunedPlayer,
    EveryXMessagesPlayer.TYPE_NAME: EveryXMessagesPlayer,
}


def llm_player_factory(player_config):
    player_class = llm_players_classes[player_config[LLM_CONFIG_KEY][ASYNC_TYPE_KEY]]
    return player_class(**player_config)

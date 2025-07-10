from game_constants import REMAINING_PLAYERS_FILE, GAME_MANAGER_NAME, MESSAGE_PARSING_PATTERN
from game_status_checks import is_nighttime
from llm_players.llm_constants import turn_task_into_prompt, EVERY_X_MESSAGES_TYPE, \
    make_more_human_like
from llm_players.llm_player import LLMPlayer


class EveryXMessagesPlayer(LLMPlayer):  # TODO implement this!

    TYPE_NAME = EVERY_X_MESSAGES_TYPE

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # TODO implement!
        # self.every_x = number_of_active_players?...
        # # scheduler_kwargs = kwargs.get("scheduler_kwargs", kwargs)
        # # self.scheduler = LLMWrapper(**scheduler_kwargs)
        # self.scheduler = self.llm  # trying to use the same one for generation...

    def should_generate_message(self, message_history):
        if is_nighttime(self.game_dir):
            every_x = 2
        else:
            every_x = len((self.game_dir / REMAINING_PLAYERS_FILE).read_text().splitlines())
        current_phase_messages = 0
        for message in message_history[::-1]:
            if f"] {GAME_MANAGER_NAME}: " in message and "voted for" in message:  # TODO constants!
                continue
            elif f"] {GAME_MANAGER_NAME}: " in message and "has ended, now it's time to vote!" in message:  # TODO constants!
                break
            if f"] " in message and ": " in message:  # TODO constants!
                current_phase_messages += 1
        self.logger.log("scheduling current_phase_messages", f"{current_phase_messages}")
        self.logger.log("scheduling every_x", f"{every_x}")
        return current_phase_messages % every_x == every_x - 1

    def generate_message(self, message_history):
        if self.should_generate_message(message_history):
            prompt = self.create_generation_prompt(message_history)
            self.logger.log("prompt in generate_message", prompt)
            message = self.llm.generate(prompt, self.get_system_info_message())
            message = make_more_human_like(message)
            return message
        else:
            return ""

    def create_generation_prompt(self, message_history):  # TODO: cuurently copied from Sche_Then_Gene...
        task = f"Add a very short message to the game's chat. " \
               f"Be specific and keep it relevant to the current situation, " \
               f"according to the last messages and the game's status. " \
               f"Your message should only be one short sentence! " \
               f"Don't add a message that you've already added (in the chat history)! " \
               f"It is very important that you don't repeat yourself! " \
               f"Match your style of message to the other player's message style, " \
               f"with more emphasis on more recent messages.\n" #\
               # f"Here are some examples of possible messages from a hypothetical game's chat, " \
               # f"as style inspiration:\n" \
               # f"\"I'm telling you guys, we can't trust Joseph\",\n" \
               # f"\"Jessica is sus\",\n" \
               # f"\"i think it is phoebe, she knew that diane was mafia and she tried to blame someone else\",\n" \
               # f"\"John is probably mafia, diane was mafia and voted for lindsay who voted for john\",\n" \
               # f"\"John is out tho\",\n" \
               # f"\"i figured out diane is mafia\",\n" \
               # f"\"jennifer is the mafia for sure! she didn't vote with us\",\n" \
               # f"\"why would i kill mafia if i was mafia ?\",\n" \
               # f"\"Jennifer is too quiet\",\n" \
               # f"\"they only try to gain our trust\",\n" \
               # f"\"exactly\",\n" \
               # f"\"because they are the only one that didnt vote diane\",\n" \
               # f"\"i think Moe is so loud\",\n" \
               # f"\"jennifer, do you have anything to say for yourself?\"...\n"
        return turn_task_into_prompt(task, message_history)

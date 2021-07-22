"""
celeste_bot.py
"""

import json
from datetime           import datetime
from random             import randint, random
from threading          import Timer
from urllib.request     import Request, urlopen, urlcleanup
from urllib.error       import HTTPError
from urllib.parse       import ParseResult, urlparse
from enum               import IntEnum
from typing             import Callable
from twitch             import TwitchHelix
from twitch.exceptions  import TwitchAttributeException, TwitchOAuthException, TwitchAuthException
from twitch.constants   import OAUTH_SCOPE_ANALYTICS_READ_EXTENSIONS

__version__ = "1.0"

# funny API cache hack, SRC admins hate him
QUERY_TABLE : dict = {
    0 : "game",
    1 : "category",
    2 : "level",
    3 : "platform",
    4 : "region",
    5 : "emulated",
    6 : "date",
    7 : "submitted",
    8 : "status",
    9 : "verify-date"
}


def print_with_timestamp(out: str) -> None:
    print(datetime.today().strftime("%d.%m.%Y %H:%M:%S").ljust(24) + str(out))


class SubmissionErrors(IntEnum):
    ERROR_SUBMITTED_RTA   = 0
    ERROR_NO_VERSION      = 1
    ERROR_INVALID_IGT     = 2
    ERROR_INVALID_VERSION = 3
    ERROR_BAD_VOD         = 4


class CelesteLeaderboardBot:
    """Class for leaderboard bot, interacting with speedrun.com API."""

    ACCOUNT_NAME : str = "BadelineBot"
    AGENT        : str = f'celeste-leaderboard-bot{__version__}'
    BASE_REASON  : Callable = lambda x : f'{CelesteLeaderboardBot.ACCOUNT_NAME} found the following problem{x} with your submission, please edit it accordingly: '
    REASON_TEXT  : dict = {
        0 : "Your submission has real-time, leave the real-time column empty",
        1 : "You did not select a version, make sure to select the correct game version",
        2 : "Your submission has an invalid IGT, check the final time of your run and adjust the submission",
        3 : "The version you selected does not exist on your platform, please select the correct game version",
        4 : "The video you submitted is a Twitch past broadcast that will be deleted after a while, please highlight your run"
    }

    def __init__(self, *, keys: dict, timer: float, games: list) -> None:
        self.q_counter  : int         = 0
        self.SRC_KEY    : str         = keys["src"]
        self.GAMES      : float       = games
        self.TIMER      : list        = timer
        self.TTV_CLIENT : TwitchHelix = TwitchHelix(
            scopes        = [OAUTH_SCOPE_ANALYTICS_READ_EXTENSIONS],
            client_id     = keys["twitch"]["client"],
            client_secret = keys["twitch"]["secret"]
        )

    @staticmethod
    def valid_real_time(run : dict) -> bool:
        """Checks if any RTA is submitted, returns False if so."""
        return run["times"]["realtime_t"] == 0

    @staticmethod
    def valid_default_version(run: dict, *, variable_id: str, default_ver: str, **_kwargs) -> bool:
        """Checks if the default version is submitted, returns False if so."""
        return not(run["values"][variable_id] == default_ver)

    @staticmethod
    def valid_in_game_time(run: dict) -> bool:
        """Checks if the submitted IGT is invalid, returns False if so"""
        return (int(1000 * run["times"]["ingame_t"]) % 17) == 0

    @staticmethod
    def valid_existing_version(run: dict, *, variable_id: str, invalid_ver: dict, **_kwargs) -> bool:
        """Checks if the submitted version is available on the submitted platform, returns False if it isn't."""
        try:
            return not(run["values"][variable_id] in invalid_ver[run["system"]["platform"]])
        # compatibility incase new platform gets added
        except KeyError:
            print_with_timestamp(f'There was an error with checking for platform of ID {run["system"]["platform"]}')
            return True

    @staticmethod
    def valid_persistent_vod(run: dict, client: TwitchHelix) -> bool:
        """Checks if submitted VOD is a past broadcast, returns False if so."""
        try:
            link_list : list = run["videos"]["links"]
            ttv_index : int  = -1
            par_res   : ParseResult
            for i, x in enumerate(link_list):
                par : ParseResult = urlparse(x["uri"])
                if 'twitch.tv' in par.netloc:
                    ttv_index = i
                    par_res = par
                    break
            # only check for twitch uri's
            if ttv_index == -1:
                return True
            else:
                try:
                    vid_id   : int  = int(par_res.path.split("/")[2])
                    vid_data : dict = client.get_videos(video_ids=[vid_id])[0]
                    if vid_data["type"] == "archive":
                        return False
                    else:
                        return True
                # catch potential errors with exctracting vid id
                except IndexError:
                    return True
                except ValueError:
                    return True
        # just in case there is no video
        except KeyError:
            return False
        # catch httperror locally
        except (TwitchAttributeException, TwitchOAuthException, TwitchAuthException) as error:
            print_with_timestamp(f'There was an error with a request on Twitch API: {error}')
            return True

    def main(self, ignore: list = [], already_rejected: list = [], loop: bool = False) -> None:
        """
            Main function.

            Checks for the validity of any new submission not in the given ignore list and rejects them if necessary.
        """
        cache    : list = []
        rejected : list = []
        # get new oauth
        try:
            self.TTV_CLIENT.get_oauth()
        except (TwitchAttributeException, TwitchOAuthException, TwitchAuthException) as error:
            print(f'There was an error with getting a Twitch OAuth token: {error}')
        # loop over all games
        for game in self.GAMES:
            faulty_runs : list = []
            try:
                urlcleanup()
                rand_d  : str     = 'desc' if random() < 0.5 else 'asc'
                get_req : Request = Request(
                    f'https://www.speedrun.com/api/v1/runs?game={game["id"]}&status=new&direction={rand_d}&orderby={QUERY_TABLE[self.q_counter]}&max={randint(100, 200)}',  # they hate me :^)
                    headers = {
                        "cache-control": "no-cache"
                    }
                )
                get_req.add_header('User-Agent', CelesteLeaderboardBot.AGENT)
                new_runs : dict = json.loads(urlopen(get_req).read())["data"]
                # loop over all new runs of a given game
                for this_run in new_runs:
                    # skip if already rejected
                    if this_run["id"] in already_rejected:
                        rejected.append(this_run["id"])
                        continue
                    # cache run for next iteration and skip if it was cached last iteration
                    cache.append(this_run["id"])
                    if this_run["id"] in ignore:
                        continue
                    # validity checks
                    invalid_run : dict = {
                        "id"     : this_run["id"],
                        "faults" : []
                    }
                    # RTA check
                    if not CelesteLeaderboardBot.valid_real_time(this_run):
                        invalid_run["faults"].append(SubmissionErrors.ERROR_SUBMITTED_RTA)
                    # default version check
                    if not CelesteLeaderboardBot.valid_default_version(this_run, **game["version"]):
                        invalid_run["faults"].append(SubmissionErrors.ERROR_NO_VERSION)
                    # IGT check
                    if not CelesteLeaderboardBot.valid_in_game_time(this_run):
                        invalid_run["faults"].append(SubmissionErrors.ERROR_INVALID_IGT)
                    # existing version check
                    if not CelesteLeaderboardBot.valid_existing_version(this_run, **game["version"]):
                        invalid_run["faults"].append(SubmissionErrors.ERROR_INVALID_VERSION)
                    # past broadcast check
                    if not CelesteLeaderboardBot.valid_persistent_vod(this_run, self.TTV_CLIENT):
                        invalid_run["faults"].append(SubmissionErrors.ERROR_BAD_VOD)
                    # push to list of faulty runs if an error was found
                    if len(invalid_run["faults"]) > 0:
                        faulty_runs.append(invalid_run)
                # loop over all invalid runs
                for this_run in faulty_runs:
                    # do PUT request
                    full_reason : str
                    x : str = 's' if len(this_run["faults"]) > 1 else ''
                    if len(this_run["faults"]) < 3:
                        full_reason = CelesteLeaderboardBot.BASE_REASON(x) \
                                    + " || ".join([CelesteLeaderboardBot.REASON_TEXT[fault] for fault in this_run["faults"]])
                    else:
                        full_reason = f'{CelesteLeaderboardBot.ACCOUNT_NAME} found various issues with your submission, please read the rules or contact a moderator/verifier.'
                    print(f'Found following problem{x} with run <{this_run["id"]}>: {this_run["faults"]}')
                    put_req : Request = Request(
                        f'https://www.speedrun.com/api/v1/runs/{this_run["id"]}/status',
                        headers = {
                            'User-Agent'    : CelesteLeaderboardBot.AGENT,
                            'Content-Type'  : 'application/json',
                            'X-API-Key'     : self.SRC_KEY,
                        },
                        data = bytes(json.dumps({
                            "status": {
                                "status": "rejected",
                                "reason": full_reason
                            }
                        }), encoding="utf-8"),
                        method = "PUT"
                    )
                    urlopen(put_req)
                    # save id and output
                    rejected.append(this_run["id"])
                    print(f'Rejected run <{this_run["id"]}> successfully')
            # invalid URI or no authorization
            except HTTPError as error:
                print(f'There was an HTTP error: {error} on {error.url}')
                cache = list(set(cache + ignore))
                rejected = list(set(rejected + already_rejected))
                break
            # connection error
            except (ConnectionResetError, ConnectionRefusedError, ConnectionAbortedError, ConnectionError) as error:
                print(f'There was a connection error: {error}')
                cache = list(set(cache + ignore))
                rejected = list(set(rejected + already_rejected))
                break
        # loop again if running from start()
        self.q_counter = (self.q_counter + 1) % len(QUERY_TABLE.keys())
        if loop: Timer(self.TIMER, self.main, [cache, rejected, loop]).start()

    def start(self) -> None:
        """Start bot, blocking calling thread."""
        self.main([], [], True)

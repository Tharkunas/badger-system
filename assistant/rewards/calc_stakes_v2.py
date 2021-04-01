from assistant.rewards.rewards_utils import calculate_sett_balances
from assistant.rewards.classes.RewardsList import RewardsList
from assistant.rewards.classes.RewardsLogger import rewardsLogger
from assistant.rewards.enums import Token
from helpers.time_utils import days, to_days, to_hours, to_utc_date
from dotmap import DotMap
from brownie import *
from rich.console import Console

console = Console()
digg = interface.IDigg(Token.digg.value)

blacklistedAddresses = [
    "0x0a54d4b378c8dbfc7bc93be50c85debafdb87439",
    "0xf9440fedc72a0b8030861dcdac39a75b544e7a3c",
]
nativeSetts = ["native.uniDiggWbtc", "native.sushiDiggWbtc"]
nonNativeSetts = [
    "native.renCrv",
    "native.sbtcCrv",
    "native.tbtcCrv",
    "native,sushiWbtcEth"
    "harvest.renCrv"
]


def calc_geyser_snapshot(badger, name, startBlock, endBlock, nextCycle, boosts, diggAllocation):

    console.log("Processing rewards for {}".format(name))
    rewards = RewardsList(nextCycle, badger.badgerTree)
    sett = badger.getSett(name)
    geyser = badger.getGeyser(name)
    startTime = web3.eth.getBlock(startBlock)["timestamp"]
    endTime = web3.eth.getBlock(endBlock)["timestamp"]

    balances = calculate_sett_balances(badger, name, sett, endBlock)
    # Get rid of blacklisted addresses
    for addr in blacklistedAddresses:
        addr = addr.lower()
        if addr in balances:
            del balances[addr]

    # Only boost non-native setts
    if name in nonNativeSetts:
        for addr, bal in balances.items():
            boostAmount = boosts.get(addr, 1)
            rewardsLogger.add_multiplier(
                web3.toChecksumAddress(addr), name, boostAmount)
            balances[addr] = bal * boostAmount

    unlockSchedules = {}
    for token in geyser.getDistributionTokens():
        unlockSchedules = parse_schedules(geyser.getUnlockSchedulesFor(token))
        rewardsLogger.add_unlock_schedules(name, token, unlockSchedules)
        tokenDistribution = int(
            get_distributed_for_token_at(token, endTime, unlockSchedules, name)
            - get_distributed_for_token_at(token,
                                           startTime, unlockSchedules, name)
        )
        rewardsLogger.add_total_token_dist(name, token, tokenDistribution)
        # Distribute to users with rewards list
        # Make sure there are tokens to distribute (some geysers only
        # distribute one token)
        if token == Token.digg.value:
            rewardsLogger.add_total_token_dist(
                name, token, digg.sharesToFragments(tokenDistribution))

            if name in nativeSetts:
                tokenDistribution = tokenDistribution * diggAllocation
            else:
                tokenDistribution = tokenDistribution * (1 - diggAllocation)

            console.log(
                "{} DIGG tokens distributed".format(
                    digg.sharesToFragments(tokenDistribution)/1e18)
            )

            rewardsLogger.add_peg_token_dist(
                name, token, digg.sharesToFragments(tokenDistribution))

        else:
            console.log(
                "{} Badger tokens distributed".format(
                    tokenDistribution/1e18)
            )
            rewardsLogger.add_peg_token_dist(name, token, tokenDistribution)

        if tokenDistribution > 0:
            rewardsUnit = tokenDistribution/sum(balances.values())
            console.log(
                "Processing rewards for {} addresses".format(len(balances)))
            for addr, balance in balances.items():
                addr = web3.toChecksumAddress(addr)
                token = web3.toChecksumAddress(token)
                rewardAmount = balance * rewardsUnit
                rewards.increase_user_rewards(addr, token, int(rewardAmount))
                rewardsLogger.add_user_token(
                    addr, name, token, int(rewardAmount))

    return rewards


def get_distributed_for_token_at(token, endTime, schedules, name):
    totalToDistribute = 0
    for index, schedule in enumerate(schedules):
        if endTime < schedule["startTime"]:
            toDistribute = 0
            console.log("\nSchedule {} for {} completed\n".format(index, name))
        else:
            rangeDuration = endTime - schedule["startTime"]
            toDistribute = min(
                schedule["initialTokensLocked"],
                int(
                    schedule["initialTokensLocked"]
                    * rangeDuration
                    // schedule["duration"]
                ),
            )
            if schedule["startTime"] <= endTime and schedule["endTime"] >= endTime:
                console.log("Tokens distributed by schedule {} at {} are {} out of {}, or {}% of total\n"
                            .format(
                                index,
                                to_utc_date(schedule["startTime"]),
                                digg.sharesToFragments(toDistribute),
                                digg.sharesToFragments(
                                    schedule["initialTokensLocked"]),
                                (int(toDistribute) /
                                 int(schedule["initialTokensLocked"])) * 100
                            )
                            )
                console.log(
                    "Total duration of schedule elapsed is {} hours out of {} hours, or {}% of total duration.\n"
                    .format(
                        to_hours(rangeDuration),
                        to_hours(schedule["duration"]),
                        rangeDuration / schedule["duration"] * 100
                    )
                )
        totalToDistribute += toDistribute
    return totalToDistribute


def parse_schedules(schedules):
    parsedSchedules = []
    for schedule in schedules:
        parsedSchedules.append(
            {
                "initialTokensLocked": schedule[0],
                "endTime": schedule[1],
                "duration": schedule[2],
                "startTime": schedule[3],
            }
        )
    return parsedSchedules

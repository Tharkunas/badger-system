import json
from brownie import *
from brownie.network.gas.strategies import GasNowStrategy
from config.rewards_config import rewards_config
from helpers.time_utils import to_hours
from rich.console import Console
from tqdm import tqdm

from assistant.rewards.aws_utils import download,upload
from assistant.rewards.calc_stakes import calc_geyser_stakes
from assistant.rewards.meta_rewards.harvest import calc_farm_rewards
from assistant.rewards.meta_rewards.sushi import calc_all_sushi_rewards
from assistant.rewards.rewards_utils import (
    sum_rewards,
    keccak,
    process_cumulative_rewards,
    combine_rewards
)
from assistant.rewards.classes.User import User
from assistant.rewards.classes.MerkleTree import rewards_to_merkle_tree
from assistant.rewards.classes.RewardsList import RewardsList
from assistant.rewards.classes.RewardsLogger import rewardsLogger

from assistant.rewards.rewards_checker import compare_rewards, verify_rewards
from scripts.systems.badger_system import BadgerSystem

gas_strategy = GasNowStrategy("fast")
console = Console()


def calc_geyser_rewards(badger, periodStartBlock, endBlock, cycle):
    """
    Calculate rewards for each geyser, and sum them
    userRewards = (userShareSeconds / totalShareSeconds) / tokensReleased
    (For each token, for the time period)
    """
    rewardsByGeyser = {}

    # For each Geyser, get a list of user to weights
    for key, geyser in badger.geysers.items():
        #if key != "native.badger":
        #      continue
        geyserRewards = calc_geyser_stakes(key, geyser, periodStartBlock, endBlock)
        rewardsByGeyser[key] = geyserRewards
    return sum_rewards(rewardsByGeyser, cycle, badger.badgerTree)


def fetchPendingMerkleData(badger):
    # currentMerkleData = badger.badgerTree.getPendingMerkleData()    
    # root = str(currentMerkleData[0])
    # contentHash = str(currentMerkleData[1])
    # lastUpdateTime = currentMerkleData[2]
    # blockNumber = currentMerkleData[3]

    root = badger.badgerTree.pendingMerkleRoot()
    contentHash = badger.badgerTree.pendingMerkleContentHash()
    lastUpdateTime = badger.badgerTree.lastProposeTimestamp()
    blockNumber = badger.badgerTree.lastProposeBlockNumber()

    return {
        "root": root,
        "contentHash": contentHash,
        "lastUpdateTime": lastUpdateTime,
        "blockNumber": int(blockNumber),
    }


def fetchCurrentMerkleData(badger):
    # currentMerkleData = badger.badgerTree.getCurrentMerkleData()
    # root = str(currentMerkleData[0])
    # contentHash = str(currentMerkleData[1])
    # lastUpdateTime = currentMerkleData[2]
    # blockNumber = badger.badgerTree.lastPublishBlockNumber()

    root = badger.badgerTree.merkleRoot()
    contentHash = badger.badgerTree.merkleContentHash()
    lastUpdateTime = badger.badgerTree.lastPublishTimestamp()
    blockNumber = badger.badgerTree.lastPublishBlockNumber()

    return {
        "root": root,
        "contentHash": contentHash,
        "lastUpdateTime": lastUpdateTime,
        "blockNumber": int(blockNumber),
    }


def getNextCycle(badger):
    return badger.badgerTree.currentCycle() + 1


def fetch_pending_rewards_tree(badger, print_output=False):
    # TODO Files should be hashed and signed by keeper to prevent tampering
    # TODO How will we upload addresses securely?
    # We will check signature before posting
    merkle = fetchPendingMerkleData(badger)
    pastFile = "rewards-1-" + str(merkle["contentHash"]) + ".json"

    if print_output:
        console.print(
            "[green]===== Loading Pending Rewards " + pastFile + " =====[/green]"
        )

    currentTree = json.loads(download(pastFile))

    # Invariant: File shoulld have same root as latest
    assert currentTree["merkleRoot"] == merkle["root"]

    lastUpdatePublish = merkle["blockNumber"]
    lastUpdate = int(currentTree["endBlock"])

    if print_output:
        print(
            "lastUpdateBlock", lastUpdate, "lastUpdatePublishBlock", lastUpdatePublish
        )
    # Ensure upload was after file tracked
    assert lastUpdatePublish >= lastUpdate

    # Ensure file tracks block within 1 day of upload
    assert abs(lastUpdate - lastUpdatePublish) < 6500

    return currentTree


def fetch_current_rewards_tree(badger, print_output=False):
    # TODO Files should be hashed and signed by keeper to prevent tampering
    # TODO How will we upload addresses securely?
    # We will check signature before posting
    merkle = fetchCurrentMerkleData(badger)
    pastFile = "rewards-1-" + str(merkle["contentHash"]) + ".json"


    console.print(
        "[bold yellow]===== Loading Past Rewards " + pastFile + " =====[/bold yellow]"
    )

    currentTree = download(pastFile)

    # Invariant: File shoulld have same root as latest
    assert currentTree["merkleRoot"] == merkle["root"]

    lastUpdateOnChain = merkle["blockNumber"]
    lastUpdate = int(currentTree["endBlock"])

    print("lastUpdateOnChain ", lastUpdateOnChain, " lastUpdate ", lastUpdate)
    # Ensure file tracks block within 1 day of upload
    # assert abs(lastUpdate - lastUpdateOnChain) < 6500

    # Ensure upload was after file tracked
    assert lastUpdateOnChain >= lastUpdate
    return currentTree


def generate_rewards_in_range(badger, startBlock, endBlock, pastRewards):
    blockDuration = endBlock - startBlock

    nextCycle = getNextCycle(badger)

    currentMerkleData = fetchCurrentMerkleData(badger)

    farmRewards = calc_farm_rewards(badger,startBlock,endBlock,nextCycle,retroactive=False)
    sushiRewards = calc_all_sushi_rewards(badger,startBlock,endBlock,nextCycle,retroactive=False)

    geyserRewards = calc_geyser_rewards(badger, startBlock, endBlock, nextCycle)

    rewardsLogger.save("rewards")

    newRewards = combine_rewards([geyserRewards,farmRewards,sushiRewards],nextCycle,badger.badgerTree)
    cumulativeRewards = process_cumulative_rewards(pastRewards, newRewards)

    # Take metadata from geyserRewards
    console.print("Processing to merkle tree")
    merkleTree = rewards_to_merkle_tree(
        cumulativeRewards, startBlock, endBlock, {}
    )

    

    # Publish data
    rootHash = keccak(merkleTree["merkleRoot"])
    contentFileName = content_hash_to_filename(rootHash)

    console.log(
        {
            "merkleRoot": merkleTree["merkleRoot"],
            "rootHash": str(rootHash),
            "contentFile": contentFileName,
            "startBlock": startBlock,
            "endBlock": endBlock,
            "currentContentHash": currentMerkleData["contentHash"],
        }
    )

    print("Uploading to file " + contentFileName)
    # TODO: Upload file to AWS & serve from server
    with open(contentFileName, "w") as outfile:
        json.dump(merkleTree, outfile,indent=4)

    with open(contentFileName) as f:
        after_file = json.load(f)

    # Sanity check new rewards file
    
    verify_rewards(
        badger,
        startBlock,
        endBlock,
        pastRewards,
        after_file,
    )

    return {
        "contentFileName": contentFileName,
        "merkleTree": merkleTree,
        "rootHash": rootHash,
    }


def rootUpdater(badger, startBlock, endBlock, pastRewards, test=False):
    """
    Root Updater Role
    - Check how much time has passed since the last published update
    - If sufficient time has passed, run the rewards script and p
    - If there is a discrepency, notify admin

    (In case of a one-off failure, Script will be attempted again at the rootUpdaterInterval)
    """
    console.print("\n[bold cyan]===== Root Updater =====[/bold cyan]\n")

    badgerTree = badger.badgerTree
    nextCycle = getNextCycle(badger)

    currentMerkleData = fetchCurrentMerkleData(badger)
    currentTime = chain.time()

    console.print(
        "\n[green]Calculate rewards for {} blocks: {} -> {} [/green]\n".format(
            endBlock - startBlock, startBlock, endBlock
        )
    )

    # Only run if we have sufficent time since previous root
    timeSinceLastupdate = currentTime - currentMerkleData["lastUpdateTime"]
    if timeSinceLastupdate < rewards_config.rootUpdateMinInterval and not test:
        console.print(
            "[bold yellow]===== Result: Last Update too Recent ({}) =====[/bold yellow]".format(
                to_hours(timeSinceLastupdate)
            )
        )
        return False

    rewards_data = generate_rewards_in_range(badger, startBlock, endBlock, pastRewards)

    console.print("===== Root Updater Complete =====")
    if not test:
        upload(contentFileName)
        badgerTree.proposeRoot(
            rewards_data["merkleTree"]["merkleRoot"],
            rewards_data["rootHash"],
            rewards_data["merkleTree"]["cycle"],
            rewards_data["merkleTree"]["startBlock"],
            rewards_data["merkleTree"]["endBlock"],
            {"from": badger.keeper, "gas_price": gas_strategy},
        )

    return True


def guardian(badger: BadgerSystem, startBlock, endBlock, pastRewards, test=False):
    """
    Guardian Role
    - Check if there is a new proposed root
    - If there is, run the rewards script at the same block height to verify the results
    - If there is a discrepency, notify admin
    (In case of a one-off failure, Script will be attempted again at the guardianInterval)
    """

    console.print("\n[bold cyan]===== Guardian =====[/bold cyan]\n")

    console.print(
        "\n[green]Calculate rewards for {} blocks: {} -> {} [/green]\n".format(
            endBlock - startBlock, startBlock, endBlock
        )
    )

    badgerTree = badger.badgerTree

    # Only run if we have a pending root
    if not badgerTree.hasPendingRoot():
        console.print("[bold yellow]===== Result: No Pending Root =====[/bold yellow]")
        return False

    rewards_data = generate_rewards_in_range(badger, startBlock, endBlock, pastRewards)

    console.print("===== Guardian Complete =====")

    if not test:
        upload(rewards_data["contentFileName"]),
        badgerTree.approveRoot(
            rewards_data["merkleTree"]["merkleRoot"],
            rewards_data["rootHash"],
            rewards_data["merkleTree"]["cycle"],
            rewards_data["merkleTree"]["startBlock"],
            rewards_data["merkleTree"]["endBlock"],
            {"from": badger.guardian, "gas_price": gas_strategy},
        )


def run_action(badger, args, test):
    if args["action"] == "rootUpdater":
        return rootUpdater(badger, args["startBlock"], args["endBlock"], args["pastRewards"], test)
    if args["action"] == "guardian":
        return guardian(badger, args["startBlock"], args["endBlock"], args["pastRewards"], test)
    return False


def content_hash_to_filename(contentHash):
    return "rewards-" + str(chain.id) + "-" + str(contentHash) + ".json"

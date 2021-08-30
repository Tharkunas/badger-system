from brownie import *
from tqdm import tqdm
from config.badger_config import badger_config
from rich.console import Console
from scripts.systems.badger_system import connect_badger
import json
from assistant.rewards.rewards_assistant import (
    process_cumulative_rewards,
    fetch_current_rewards_tree,
)
from assistant.rewards.rewards_checker import test_claims
from config.rewards_config import rewards_config
from assistant.rewards.classes.MerkleTree import rewards_to_merkle_tree
from assistant.rewards.classes.RewardsLog import rewardsLog
from assistant.rewards.meta_rewards.airdrop_rewards import calc_airdrop_rewards

console = Console()

def main():
    test = False
    badger = connect_badger(badger_config.prod_json, load_deployer=False)
    nextCycle = badger.badgerTree.currentCycle() + 1
    rewards = calc_airdrop_rewards(badger, nextCycle)
    
    rewardsLog.save("dropt-airdrop")
    currentRewards = fetch_current_rewards_tree(badger)
    cumulative_rewards = process_cumulative_rewards(currentRewards, rewards)

    startBlock = currentRewards["startBlock"]
    endBlock = currentRewards["endBlock"]

    merkleTree = rewards_to_merkle_tree(cumulative_rewards, currentRewards["startBlock"], currentRewards["endBlock"], {})
    rootHash = web3.toHex(web3.keccak(text=merkleTree["merkleRoot"]))

    contentFileName = (
        "rewards-" + str(chain.id) + "-" + str(merkleTree["merkleRoot"]) + ".json"
    )
    console.log("Saving merkle tree as {}".format(contentFileName))
    with open(contentFileName, "w") as f:
        json.dump(merkleTree, f, indent=4)

    if not test:
        badger.badgerTree.proposeRoot(
            merkleTree["merkleRoot"],
            rootHash,
            nextCycle,
            startBlock,
            endBlock,
            {"from": badger.root_proposer},
        )

        badger.badgerTree.approveRoot(
            merkleTree["merkleRoot"],
            rootHash,
            nextCycle,
            startBlock,
            endBlock,
            {"from": badger.root_approver},
        )

        # test_claims(badger, startBlock, endBlock, currentRewards ,merkleTree)
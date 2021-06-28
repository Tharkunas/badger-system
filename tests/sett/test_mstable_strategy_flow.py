from helpers.time_utils import days, hours
import brownie
import pytest
from brownie import *
from helpers.constants import *
from helpers.sett.SnapshotManager import SnapshotManager
from tests.conftest import badger_single_sett, settTestConfig
from tests.helpers import distribute_from_whales, getTokenMetadata
from tests.test_recorder import EventRecord, TestRecorder
from rich.console import Console

console = Console()

# @pytest.mark.skip()
@pytest.mark.parametrize(
    "settConfig", settTestConfig,
)
def test_deposit_withdraw_single_user_flow(settConfig):
    badger = badger_single_sett(settConfig)

    controller = badger.getController(settConfig["id"])
    sett = badger.getSett(settConfig["id"])
    strategy = badger.getStrategy(settConfig["id"])
    want = badger.getStrategyWant(settConfig["id"])

    settKeeper = accounts.at(sett.keeper(), force=True)
    strategyKeeper = accounts.at(strategy.keeper(), force=True)

    snap = SnapshotManager(badger, settConfig["id"])

    deployer = badger.deployer

    governance = strategy.governance()

    randomUser = accounts[6]

    assert want.balanceOf(deployer) >= 0

    depositAmount = int(want.balanceOf(deployer) * 0.8)
    assert depositAmount > 0

    want.approve(sett, MaxUint256, {"from": deployer})
    snap.settDeposit(depositAmount, {"from": deployer})

    # Earn
    with brownie.reverts("onlyAuthorizedActors"):
        sett.earn({"from": randomUser})

    min = sett.min()
    max = sett.max()
    remain = max - min

    snap.settEarn({"from": settKeeper})

    chain.sleep(15)
    chain.mine(1)

    snap.settWithdraw(depositAmount // 2, {"from": deployer})

    chain.sleep(10000)
    chain.mine(1)

    snap.settWithdraw(depositAmount // 2 - 1, {"from": deployer})

    # assert False

# @pytest.mark.skip()
@pytest.mark.parametrize(
    "settConfig", settTestConfig,
)
def test_single_user_harvest_flow(settConfig):
    badger = badger_single_sett(settConfig)

    controller = badger.getController(settConfig["id"])
    sett = badger.getSett(settConfig["id"])
    strategy = badger.getStrategy(settConfig["id"])
    want = badger.getStrategyWant(settConfig["id"])

    settKeeper = accounts.at(sett.keeper(), force=True)
    strategyKeeper = accounts.at(strategy.keeper(), force=True)

    snap = SnapshotManager(badger, settConfig["id"])

    deployer = badger.deployer
    randomUser = accounts[6]

    startingBalance = want.balanceOf(deployer)

    depositAmount = startingBalance // 2
    assert startingBalance >= depositAmount
    assert startingBalance >= 0

    # Deposit
    want.approve(sett, MaxUint256, {"from": deployer})
    snap.settDeposit(depositAmount, {"from": deployer})

    assert want.balanceOf(sett) > 0
    print("want.balanceOf(sett)", want.balanceOf(sett))

    # Earn
    snap.settEarn({"from": settKeeper})

    chain.sleep(days(0.5))
    chain.mine()

    chain.sleep(days(1))
    chain.mine()

    with brownie.reverts("onlyAuthorizedActors"):
        strategy.harvest({"from": randomUser})

    # Harvest
    snap.settHarvest({"from": strategyKeeper})

    chain.sleep(days(1))
    chain.mine()

    # Withdraw
    snap.settWithdraw(depositAmount // 2, {"from": deployer})

    # Chain sleeps for more than 6 months to allow for a mta full vesting cycle
    chain.sleep(days(186))
    chain.mine()

    # Harvest
    snap.settHarvest({"from": strategyKeeper})

    # Withdraw
    snap.settWithdraw(depositAmount // 2 - 1, {"from": deployer})

    # assert False

# @pytest.mark.skip()
@pytest.mark.parametrize(
    "settConfig", settTestConfig,
)
def test_voterproxy_loan(settConfig):
    badger = badger_single_sett(settConfig)

    controller = badger.getController(settConfig["id"])
    sett = badger.getSett(settConfig["id"])
    strategy = badger.getStrategy(settConfig["id"])
    want = badger.getStrategyWant(settConfig["id"])
    voterproxy = badger.mstable.voterproxy

    badgerGovernance = accounts.at(badger.mstable.voterproxy.badgerGovernance(), force=True)
    settKeeper = accounts.at(sett.keeper(), force=True)
    strategyKeeper = accounts.at(strategy.keeper(), force=True)

    snap = SnapshotManager(badger, settConfig["id"])

    deployer = badger.deployer

    governance = strategy.governance()

    randomUser = accounts[6]

    mta = ERC20.at(strategy.mta())
    
    mtaBalance = mta.balanceOf(deployer.address)
    assert mtaBalance > 0

    # == Deployer loans total MTA balance to VoterProxy == #
    print("Deployer loans: ", mtaBalance)
    mta.approve(voterproxy.address, MaxUint256, {"from": deployer})
    voterproxy.loan(mtaBalance, {"from": deployer})

    assert mta.balanceOf(deployer.address) == 0
    assert mta.balanceOf(voterproxy.address) == mtaBalance
    assert voterproxy.loans(deployer.address) == mtaBalance

    chain.sleep(days(1))
    chain.mine()

    # == Deposit -> Earn -> Harvest -> Withdraw flow == #

    startingBalance = want.balanceOf(deployer)

    depositAmount = startingBalance // 2
    assert startingBalance >= depositAmount
    assert startingBalance >= 0

    # Deposit
    want.approve(sett, MaxUint256, {"from": deployer})
    snap.settDeposit(depositAmount, {"from": deployer})

    assert want.balanceOf(sett) > 0

    # Earn
    snap.settEarn({"from": settKeeper})

    chain.sleep(days(2))
    chain.mine()

    # Harvest
    snap.settHarvest({"from": strategyKeeper})

    chain.sleep(days(2))
    chain.mine()

    # Withdraw
    snap.settWithdraw(depositAmount // 2, {"from": deployer})

    chain.sleep(days(2))
    chain.mine()


    # == Full loan is repayed to Deployer == #
    voterproxy.repayLoan(deployer.address, {"from": badgerGovernance})

    assert mta.balanceOf(deployer.address) == mtaBalance
    # Some MTA must have been accumulated from harvest flow
    assert mta.balanceOf(voterproxy.address) > 0
    assert voterproxy.loans(deployer.address) == 0

    # assert False





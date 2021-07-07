from ape_safe import ApeSafe
from helpers.gnosis_safe import GnosisSafe
from helpers.token_utils import distribute_test_ether
from scripts.systems.constants import SettType
from scripts.systems.badger_system import BadgerSystem, connect_badger
from scripts.systems.digg_system import connect_digg
from config.badger_config import badger_config, sett_config, digg_config
from brownie import *
import json
from helpers.constants import AddressZero

class DiggStabilizeMiniDeploy:
    def deploy(self, sett_type=SettType.DEFAULT, deploy=True) -> BadgerSystem:
        badger = connect_badger()

        digg = badger.digg
        dev = badger.deployer

        id = "experimental.digg"

        timelock = badger.digg.daoDiggTimelock

        multi = GnosisSafe(badger.devMultisig)
        safe = ApeSafe(badger.devMultisig.address)
        ops = ApeSafe(badger.opsMultisig.address)

        # controller = ops.contract(badger.getController("experimental").address)
        controller = Controller.at(badger.getController("experimental").address)

        # devMultisig
        governance = accounts.at(controller.governance(), force=True)

        stabilizeVault = "0xE05D2A6b97dce9B8e59ad074c2E4b6D51a24aAe3"
        diggTreasury = DiggTreasury.deploy({"from": dev})

        strategy = StabilizeStrategyDiggV1.deploy({"from": dev})
        strategy.initialize(
            governance.address,
            dev,
            controller,
            badger.keeper,
            badger.guardian,
            0,
            [stabilizeVault, diggTreasury],
            [250, 0, 50, 250],
            {"from": dev},
        )

        badger.sett_system.strategies[id] = strategy

        diggTreasury.initialize(strategy, {"from": dev})

        """
            address _governance,
            address _strategist,
            address _controller,
            address _keeper,
            address _guardian,
            uint256 _lockedUntil,
            address[2] memory _vaultConfig,
            uint256[4] memory _feeConfig
        """

        with open(digg_config.prod_json) as f:
            badger_deploy = json.load(f)

        vault = StabilizeDiggSett.at(
            badger_deploy["sett_system"]["vaults"]["experimental.digg"]
        )

        badger.sett_system.vaults[id] = vault

        # Used to deploy vault locally:

        # vault = StabilizeDiggSett.deploy({"from": dev})
        # vault.initialize(
        #     digg.token,
        #     controller,
        #     governance.address,
        #     badger.keeper,
        #     badger.guardian,
        #     False,
        #     "",
        #     "",
        # ),

        digg = connect_digg(digg_config.prod_json)
        self.digg = digg
        self._deploy_dynamic_oracle(self.digg.devMultisig)

        print("governance", controller.governance())

        # Wire up strategy:
        controller.approveStrategy(digg.token, strategy.address, {"from": governance})
        controller.setStrategy(digg.token, strategy.address, {"from": governance})
        # controller.setVault(digg.token, vault.address, {"from": governance})

        assert controller.strategies(vault.token()) == strategy.address
        assert controller.vaults(strategy.want()) == vault.address

        # Add actors to guestList if existing:
        if vault.guestList() != AddressZero:
            guestlist = VipCappedGuestListBbtcUpgradeable.at(
                vault.guestList()
            )
            owner = accounts.at(guestlist.owner(), force=True)

            guestlist.setGuests(
                [badger.deployer.address, accounts[6].address],
                [True, True],
                {"from": owner},
            )
        
        badger.controller = controller
        badger.strategy = strategy
        badger.vault = vault
        badger.digg = self.digg

        self.badger = badger
        return self.badger

    def _deploy_dynamic_oracle(self, owner):
        # Deploy dynamic oracle (used for testing ONLY).
        self.digg.deploy_dynamic_oracle()
        # Authorize dynamic oracle as a data provider to median oracle.
        self.digg.marketMedianOracle.addProvider(
            self.digg.dynamicOracle, {"from": owner},
        )

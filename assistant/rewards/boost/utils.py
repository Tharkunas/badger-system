from assistant.rewards.classes.UserBalance import UserBalance, UserBalances
from assistant.rewards.snapshot.utils import chain_snapshot
from assistant.badger_api.prices import (
    fetch_token_prices,
)

prices = fetch_token_prices()


def calc_union_addresses(nativeSetts: UserBalances, nonNativeSetts: UserBalances):
    """
    Combine addresses from native setts and non native setts
    :param nativeSetts: native setts
    :param nonNativeSetts: non native setts
    """
    return set.union(
        *[
            {user.address for user in nativeSetts},
            {user.address for user in nonNativeSetts},
        ]
    )


def filter_dust(balances: UserBalances, dustAmount: int):
    """
    Filter out dust values from user balances
    :param balances: balances to filter
    :param dustAmount: dollar amount to filter by
    """
    return UserBalances(
        list(filter(lambda user: user.balance > dustAmount, balances)),
        balances.settType,
        balances.settRatio,
    )


def convert_balances_to_usd(balances: userBalances):
    """
    Convert sett balance to usd and multiply by correct ratio
    :param balances: balances to convert to usd
    """
    price = prices[tokenAddress]
    priceRatio = balances.settRatio
    for user in balances:
        user.balance = settRatio * price * user.balance

    return userBalances


def calc_boost_data(badger: BadgerSystem, block: int):
    """
    Calculate boost data required for boost calculation
    :param badger: badger system
    :param block: block to collect the boost data from
    """
    chains = ["eth", "bsc", "polygon", "xdai"]
    ethSnapshot = chain_snapshot("eth", badger, block)
    bscSnapshot = chain_snapshot("eth", badger, block)
    polygonSnapshot = chain_snapshot("polygon", badger, block)
    ## Figure out how to map blocks, maybe  time -> block per chain

    native = {}
    nonNative = {}

    for chain in chains:
        snapshot = chain_snapshot(chain, badger, block)
        for sett, balances in snapshot.items():
            balances = convert_balances_to_usd(balances)
            if balances.settType == "native":
                native = dict(Counter(balances) + Counter(native))
            elif balances.settType == "nonNative":
                nonNative = dict(Counter(balances) + Counter(nonNative))

    return (filter_dust(UserBalances(native, "", ""), 1),)
    filter_dust(UserBalances(nonNative, "", ""), 1)

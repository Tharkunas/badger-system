pragma solidity 0.4.24;

import "deps/openzeppelin-eth/2.0.2/contracts/math/SafeMath.sol";
import "deps/openzeppelin-eth/2.0.2/contracts/ownership/Ownable.sol";
import "deps/openzeppelin-eth/2.0.2/contracts/token/ERC20/SafeERC20.sol";
import "deps/openzeppelin-eth/2.0.2/contracts/token/ERC20/ERC20Detailed.sol";

import "./lib/SafeMathInt.sol";

/**
 * @title uFragments ERC20 token
 * @dev This is part of an implementation of the uFragments Ideal Money protocol.
 *      uFragments is a normal ERC20 token, but its supply can be adjusted by splitting and
 *      combining tokens proportionally across all wallets.
 *
 *      uFragment balances are internally represented with a hidden denomination, 'shares'.
 *      We support splitting the currency in expansion and combining the currency on contraction by
 *      changing the exchange rate between the hidden 'shares' and the public 'fragments'.
 */

contract UFragments is ERC20Detailed, Ownable {
    // PLEASE READ BEFORE CHANGING ANY ACCOUNTING OR MATH
    // Anytime there is division, there is a risk of numerical instability from rounding errors. In
    // order to minimize this risk, we adhere to the following guidelines:
    // 1) The conversion rate adopted is the number of shares that equals 1 fragment.
    //    The inverse rate must not be used--TOTAL_SHARES is always the numerator and _totalSupply is
    //    always the denominator. (i.e. If you want to convert shares to fragments instead of
    //    multiplying by the inverse rate, you should divide by the normal rate)
    // 2) Share balances converted into Fragments are always rounded down (truncated).
    //
    // We make the following guarantees:
    // - If address 'A' transfers x Fragments to address 'B'. A's resulting external balance will
    //   be decreased by precisely x Fragments, and B's external balance will be precisely
    //   increased by x Fragments.
    //
    // We do not guarantee that the sum of all balances equals the result of calling totalSupply().
    // This is because, for any conversion function 'f()' that has non-zero rounding error,
    // f(x0) + f(x1) + ... + f(xn) is not always equal to f(x0 + x1 + ... xn).
    using SafeMath for uint256;
    using SafeMathInt for int256;
    using SafeERC20 for IERC20;

    event LogRebase(uint256 indexed epoch, uint256 totalSupply);
    event LogMonetaryPolicyUpdated(address monetaryPolicy);
    event RebaseToggled(bool rebasePaused);

    // Used for authentication
    address public monetaryPolicy;
    uint256 public rebaseStartTime;

    modifier onlyMonetaryPolicy() {
        require(msg.sender == monetaryPolicy);
        _;
    }

    // Reactivated rebasePaused flag per BIP92 (https://forum.badger.finance/t/bip-92-digg-restructuring-v3-revised/5653)
    bool private rebasePaused;
    bool private tokenPausedDeprecated;

    modifier validRecipient(address to) {
        require(to != address(0x0));
        require(to != address(this));
        _;
    }

    modifier onlyAfterRebaseStart() {
        require(now >= rebaseStartTime);
        _;
    }

    uint256 private constant DECIMALS = 9;
    uint256 private constant SCALED_SHARES_EXTRA_DECIMALS = 9;
    uint256 private constant MAX_UINT256 = ~uint256(0);
    uint256 private constant MAX_UINT128 = ~uint128(0);
    uint256 private constant MAX_FRAGMENTS_SUPPLY = 4000 * 10**DECIMALS;

    // TOTAL_SHARES is a multiple of MAX_FRAGMENTS_SUPPLY so that _sharesPerFragment is an integer.
    // Use the highest value that fits in a uint128 for sufficient granularity.
    uint256 private constant TOTAL_SHARES = MAX_UINT256 - (MAX_UINT256 % MAX_FRAGMENTS_SUPPLY);

    // MAX_SUPPLY = maximum integer < (sqrt(4*TOTAL_SHARES + 1) - 1) / 2
    uint256 private constant MAX_SUPPLY = MAX_UINT128;

    uint256 private _totalSupply;
    uint256 public _sharesPerFragment;
    uint256 public _initialSharesPerFragment;
    mapping(address => uint256) private _shareBalances;

    // This is denominated in Fragments, because the shares-fragments conversion might change before
    // it's fully paid.
    mapping(address => mapping(address => uint256)) private _allowedFragments;

    // Data for minting remDIGG
    address private constant TREASURY_OPS_MSIG = 0x042B32Ac6b453485e357938bdC38e0340d4b9276;
    uint256 private constant MINT_AMOUNT = 52942035500;
    bool private remDiggMint;

    /**
     * @param monetaryPolicy_ The address of the monetary policy contract to use for authentication.
     */
    function setMonetaryPolicy(address monetaryPolicy_) external onlyOwner {
        monetaryPolicy = monetaryPolicy_;
        emit LogMonetaryPolicyUpdated(monetaryPolicy_);
    }

    /**
     * @dev Notifies Fragments contract about a new rebase cycle.
     * @param supplyDelta The number of new fragment tokens to add into circulation via expansion.
     * @return The total number of fragments after the supply adjustment.
     */
    function rebase(uint256 epoch, int256 supplyDelta) external onlyMonetaryPolicy onlyAfterRebaseStart returns (uint256) {
        require(!rebasePaused, "Rebase paused");

        if (supplyDelta == 0) {
            emit LogRebase(epoch, _totalSupply);
            return _totalSupply;
        }

        if (supplyDelta < 0) {
            _totalSupply = _totalSupply.sub(uint256(supplyDelta.abs()));
        } else {
            _totalSupply = _totalSupply.add(uint256(supplyDelta));
        }

        if (_totalSupply > MAX_SUPPLY) {
            _totalSupply = MAX_SUPPLY;
        }

        _sharesPerFragment = TOTAL_SHARES.div(_totalSupply);

        // From this point forward, _sharesPerFragment is taken as the source of truth.
        // We recalculate a new _totalSupply to be in agreement with the _sharesPerFragment
        // conversion rate.
        // This means our applied supplyDelta can deviate from the requested supplyDelta,
        // but this deviation is guaranteed to be < (_totalSupply^2)/(TOTAL_SHARES - _totalSupply).
        //
        // In the case of _totalSupply <= MAX_UINT128 (our current supply cap), this
        // deviation is guaranteed to be < 1, so we can omit this step. If the supply cap is
        // ever increased, it must be re-included.
        // NB: Digg will likely never reach the total supply cap as the total supply of BTC is
        // currently 21 million and MAX_UINT128 is many orders of magnitude greater.
        // _totalSupply = TOTAL_SHARES.div(_sharesPerFragment)

        emit LogRebase(epoch, _totalSupply);
        return _totalSupply;
    }

    function initialize(address owner_) public initializer {
        ERC20Detailed.initialize("Digg", "DIGG", uint8(DECIMALS));
        Ownable.initialize(owner_);

        rebaseStartTime = 0;
        rebasePaused = true;
        tokenPausedDeprecated = false;

        _totalSupply = MAX_FRAGMENTS_SUPPLY;
        _shareBalances[owner_] = TOTAL_SHARES;
        _sharesPerFragment = TOTAL_SHARES.div(_totalSupply);
        _initialSharesPerFragment = TOTAL_SHARES.div(_totalSupply);

        emit Transfer(address(0x0), owner_, _totalSupply);
    }

    /**
     * @return The total number of fragments.
     */
    function totalSupply() public view returns (uint256) {
        return _totalSupply;
    }

    /**
     * @return The total number of underlying shares.
     */
    function totalShares() public view returns (uint256) {
        return TOTAL_SHARES;
    }

    /**
     * @param who The address to query.
     * @return The balance of the specified address.
     */
    function balanceOf(address who) public view returns (uint256) {
        return _shareBalances[who].div(_sharesPerFragment);
    }

    /**
     * @param who The address to query.
     * @return The underlying shares of the specified address.
     */
    function sharesOf(address who) public view returns (uint256) {
        return _shareBalances[who];
    }

    /**
     * @param fragments Fragment value to convert.
     * @return The underlying share value of the specified fragment amount.
     */
    function fragmentsToShares(uint256 fragments) public view returns (uint256) {
        return fragments.mul(_sharesPerFragment);
    }

    /**
     * @param shares Share value to convert.
     * @return The current fragment value of the specified underlying share amount.
     */
    function sharesToFragments(uint256 shares) public view returns (uint256) {
        if (shares == 0) {
            return 0;
        }
        return shares.div(_sharesPerFragment);
    }

    /// @dev Scaled Shares are a user-friendly representation of shares
    function scaledSharesToShares(uint256 fragments) public view returns (uint256) {
        return fragments.mul(_initialSharesPerFragment).mul(10**SCALED_SHARES_EXTRA_DECIMALS);
    }

    function sharesToScaledShares(uint256 shares) public view returns (uint256) {
        if (shares == 0) {
            return 0;
        }
        return shares.div(_initialSharesPerFragment).mul(10**SCALED_SHARES_EXTRA_DECIMALS);
    }

    /**
     * @dev Transfer tokens to a specified address.
     * @param to The address to transfer to.
     * @param value The amount to be transferred.
     * @return True on success, false otherwise.
     */
    function transfer(address to, uint256 value) public validRecipient(to) returns (bool) {
        uint256 shareValue = value.mul(_sharesPerFragment);
        _shareBalances[msg.sender] = _shareBalances[msg.sender].sub(shareValue);
        _shareBalances[to] = _shareBalances[to].add(shareValue);
        emit Transfer(msg.sender, to, value);
        return true;
    }

    /**
     * @dev Function to check the amount of tokens that an owner has allowed to a spender.
     * @param owner_ The address which owns the funds.
     * @param spender The address which will spend the funds.
     * @return The number of tokens still available for the spender.
     */
    function allowance(address owner_, address spender) public view returns (uint256) {
        return _allowedFragments[owner_][spender];
    }

    /**
     * @dev Transfer tokens from one address to another.
     * @param from The address you want to send tokens from.
     * @param to The address you want to transfer to.
     * @param value The amount of tokens to be transferred.
     */
    function transferFrom(
        address from,
        address to,
        uint256 value
    ) public validRecipient(to) returns (bool) {
        _allowedFragments[from][msg.sender] = _allowedFragments[from][msg.sender].sub(value);

        uint256 shareValue = value.mul(_sharesPerFragment);
        _shareBalances[from] = _shareBalances[from].sub(shareValue);
        _shareBalances[to] = _shareBalances[to].add(shareValue);
        emit Transfer(from, to, value);

        return true;
    }

    /**
     * @dev Approve the passed address to spend the specified amount of tokens on behalf of
     * msg.sender. This method is included for ERC20 compatibility.
     * increaseAllowance and decreaseAllowance should be used instead.
     * Changing an allowance with this method brings the risk that someone may transfer both
     * the old and the new allowance - if they are both greater than zero - if a transfer
     * transaction is mined before the later approve() call is mined.
     *
     * @param spender The address which will spend the funds.
     * @param value The amount of tokens to be spent.
     */
    function approve(address spender, uint256 value) public returns (bool) {
        _allowedFragments[msg.sender][spender] = value;
        emit Approval(msg.sender, spender, value);
        return true;
    }

    /**
     * @dev Increase the amount of tokens that an owner has allowed to a spender.
     * This method should be used instead of approve() to avoid the double approval vulnerability
     * described above.
     * @param spender The address which will spend the funds.
     * @param addedValue The amount of tokens to increase the allowance by.
     */
    function increaseAllowance(address spender, uint256 addedValue) public returns (bool) {
        _allowedFragments[msg.sender][spender] = _allowedFragments[msg.sender][spender].add(addedValue);
        emit Approval(msg.sender, spender, _allowedFragments[msg.sender][spender]);
        return true;
    }

    /**
     * @dev Decrease the amount of tokens that an owner has allowed to a spender.
     *
     * @param spender The address which will spend the funds.
     * @param subtractedValue The amount of tokens to decrease the allowance by.
     */
    function decreaseAllowance(address spender, uint256 subtractedValue) public returns (bool) {
        uint256 oldValue = _allowedFragments[msg.sender][spender];
        if (subtractedValue >= oldValue) {
            _allowedFragments[msg.sender][spender] = 0;
        } else {
            _allowedFragments[msg.sender][spender] = oldValue.sub(subtractedValue);
        }
        emit Approval(msg.sender, spender, _allowedFragments[msg.sender][spender]);
        return true;
    }

    /**
     * @notice Mints the reimbursement for remDIGG one time, directly to dev multisig
     * @dev This is implemented to address BIP92 (https://forum.badger.finance/t/bip-92-digg-restructuring-v3-revised/5653)
     * @dev by allowing the development multisig a one time mint of the totalSupply of remDIGG for distribution
     */
    function oneTimeMint() external onlyOwner {
        require(!remDiggMint, "Mint already complete");
        uint256 shareValue = MINT_AMOUNT.mul(_sharesPerFragment);
        _shareBalances[TREASURY_OPS_MSIG] = _shareBalances[TREASURY_OPS_MSIG].add(shareValue);
        _totalSupply = _totalSupply.add(MINT_AMOUNT);
        remDiggMint = true;
        emit Transfer(address(0x0), TREASURY_OPS_MSIG, MINT_AMOUNT);
    }

    /**
     * @notice Sweep unprotected tokens to the owner contract to recover them from the contract
     * @param _token token to sweep from the contract
     * @dev this contract should never hold any tokens so there are no protected tokens
     */
    function sweep(IERC20 _token) external onlyOwner {
        require(_token.balanceOf(address(this)) > 0, "No balance to sweep");
        _token.safeTransfer(owner(), _token.balanceOf(address(this)));
    }

    /// @notice Toggle rebase functionality
    function toggleRebase() external onlyOwner {
        rebasePaused = !rebasePaused;
        emit RebaseToggled(rebasePaused);
    }
}

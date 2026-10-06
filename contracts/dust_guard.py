"""DustGuard: the on-chain referee for a dust sale (Algorand Python / algopy, ARC-4).

A dust sale is one atomic group the owner signs: Tinyman swaps of their tokens for ALGO, close-outs
that free 0.1 ALGO each, and at most one payment of the fee. ``begin`` opens the group and ``end``
closes it; between them this contract only watches. Built after ChuuPay's contract: say exactly
what code enforces and what it does not.

**Enforced by code, in every group that calls begin and end.**

* The fee is at most 1% of what the owner gained, measured as their spendable ALGO (balance minus
  minimum balance) at ``end`` against ``begin``. ``FEE_BPS`` is a constant, not state.
* The owner comes out ahead or the whole group fails.
* ``begin`` is first and ``end`` is last, so nothing can happen outside the measurement.
* Only three kinds of transaction may appear: payments, asset transfers and plain application calls.
  No asset configuration (which could hand away an asset's manager or clawback role), no freeze, no
  key registration, no opt-out of an app.
* The only ALGO the owner may send is one fee payment, to the fee address fixed at creation.
* Every token the owner sends goes into a Tinyman sale: each transfer with an amount is followed by a
  call to the exchange fixed at creation, which checks the transfer goes to its pool. A close-out
  sends any remainder to that same pool. So no coin can reach the operator or anyone else.
* The only applications called are this one and the exchange.
* Every transaction is the owner's own, and none rekeys (hands their signing to another key).
* This contract holds nothing and sends nothing: it has no inner transactions at all.
* It cannot be updated or deleted, so none of the above can be swapped out later.
* Public totals: sweeps, ALGO returned to owners, fees taken, in global state for anyone to read.

**Not enforced by code.** A page could build a sale without calling this contract; the wallet shows
every transaction before signing, and a group with no ``begin`` and ``end`` is not refereed. Prices
come from Tinyman, not from here. If Tinyman replaces its exchange application, a new guard is needed.

Compile::

    puyapy contracts/dust_guard.py --out-dir artifacts --output-bytecode   (out-dir is relative to this file)
"""
from algopy import (ARC4Contract, Account, Global, GlobalState, OnCompleteAction, TransactionType, Txn, UInt64,
                    arc4, gtxn, subroutine, urange)

FEE_BPS = 100           # 1%, in basis points (hundredths of a percent)
BPS = 10_000


@subroutine
def spendable(a: Account) -> UInt64:
    """ALGO the account can move: its balance less the minimum balance its holdings lock."""
    return a.balance - a.min_balance


class DustGuard(ARC4Contract):
    def __init__(self) -> None:
        self.fee_address = GlobalState(Account)
        self.exchange = GlobalState(UInt64)
        self.owner = GlobalState(Account)
        self.start = GlobalState(UInt64)
        self.sweeps = GlobalState(UInt64)
        self.returned = GlobalState(UInt64)
        self.fees = GlobalState(UInt64)

    @arc4.abimethod(create="require")
    def create(self, fee_address: Account, exchange: UInt64) -> None:
        self.fee_address.value = fee_address
        self.exchange.value = exchange
        self.owner.value = Global.zero_address
        self.start.value = UInt64(0)
        self.sweeps.value = UInt64(0)
        self.returned.value = UInt64(0)
        self.fees.value = UInt64(0)

    @arc4.abimethod
    def begin(self) -> None:
        assert Txn.group_index == 0 or self.created_just_before(), "begin must be first"
        last = gtxn.ApplicationCallTransaction(Global.group_size - 1)
        assert last.app_id == Global.current_application_id, "end must close the group"
        assert Txn.sender != self.fee_address.value, "the operator cannot sell to itself"
        self.owner.value = Txn.sender
        self.start.value = spendable(Txn.sender)

    @subroutine
    def created_just_before(self) -> bool:
        """A witness run creates this app in transaction 0 and calls begin in transaction 1."""
        if Txn.group_index != 1:
            return False
        first = gtxn.Transaction(0)
        return first.type == TransactionType.ApplicationCall and first.created_app == Global.current_application_id

    @arc4.abimethod
    def end(self) -> UInt64:
        owner = self.owner.value
        assert Txn.group_index == Global.group_size - 1, "end must be last"
        assert Txn.sender == owner, "begin and end must be the same owner"
        fee = UInt64(0)
        for i in urange(Global.group_size):
            t = gtxn.Transaction(i)
            assert t.sender == owner, "every transaction is the owner's"
            assert t.rekey_to == Global.zero_address, "no rekey"
            if t.type == TransactionType.Payment:
                assert t.receiver == self.fee_address.value, "the only ALGO sent is the fee"
                assert t.close_remainder_to == Global.zero_address, "no closing an account"
                assert fee == 0, "one fee payment at most"
                fee = t.amount
            elif t.type == TransactionType.AssetTransfer:
                self.check_transfer(i)
            elif t.type == TransactionType.ApplicationCall:
                assert t.on_completion == OnCompleteAction.NoOp, "plain calls only"
                assert (t.app_id == Global.current_application_id or t.app_id.id == self.exchange.value
                        or t.created_app == Global.current_application_id), "only this app and the exchange"
            else:
                assert False, "only payments, asset transfers and application calls"
        before_fee = spendable(owner) + fee
        assert before_fee >= self.start.value, "the owner must not end up behind"
        gain = before_fee - self.start.value
        assert fee * BPS <= gain * FEE_BPS, "fee over 1%"
        self.sweeps.value += 1
        self.returned.value += gain - fee
        self.fees.value += fee
        self.owner.value = Global.zero_address
        self.start.value = UInt64(0)
        return fee

    @subroutine
    def check_transfer(self, i: UInt64) -> None:
        """A token sent is a Tinyman sale; a close-out returns any remainder to the pool of that sale."""
        t = gtxn.AssetTransferTransaction(i)
        if t.asset_amount > 0:
            assert i + 1 < Global.group_size, "a sale needs the exchange call after it"
            swap = gtxn.Transaction(i + 1)
            assert swap.type == TransactionType.ApplicationCall, "a sale needs the exchange call after it"
            assert swap.app_id.id == self.exchange.value, "a sale needs the exchange call after it"
        if t.asset_close_to != Global.zero_address:
            assert i >= 2, "a close-out follows its sale"
            sale = gtxn.Transaction(i - 2)
            assert sale.type == TransactionType.AssetTransfer, "a close-out follows its sale"
            assert sale.xfer_asset == t.xfer_asset, "a close-out follows its sale"
            assert t.asset_close_to == sale.asset_receiver, "the remainder goes to the same pool"

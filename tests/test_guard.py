"""DustGuard, tested against mainnet with algod simulate: nothing is deployed, signed or submitted.

Each cheat differs from an honest control group by one change, and must fail at the guard.
The owner and the fee address are public holders of a pooled token, picked at run time.

    .venv/Scripts/python.exe -m pytest tests -q
"""
import json, sys, urllib.request
from pathlib import Path

import pytest
from algosdk import transaction
from algosdk.v2client import algod
from tinyman.v2.client import TinymanV2MainnetClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import group, scan  # noqa: E402

TOKEN = 470842789            # Defly Token: has a Tinyman ALGO pool
INDEXER = 'https://mainnet-idx.algonode.cloud/v2'
ARTIFACTS = group.ARTIFACTS


def get(url):
    return json.load(urllib.request.urlopen(url, timeout=20))


@pytest.fixture(scope='module')
def chain():
    client = algod.AlgodClient('', scan.ALGOD.rsplit('/v2', 1)[0])
    holders = get(f'{INDEXER}/assets/{TOKEN}/balances?currency-greater-than=100000000&limit=20')['balances']
    accts = [client.account_info(h['address']) for h in holders]
    owner, fee_to, third = [a for a in accts if a['amount'] - a['min-balance'] > 1_000_000][:3]
    round_ = client.status()['last-round']
    return dict(client=client, owner=owner, fee_to=fee_to['address'], third=third['address'], round=round_,
                app=group.next_app_id(round_), params=client.suggested_params())


def build(c, fee_bps=group.FEE_BPS, tamper=None, end=True, amount=100_000_000, close=False):
    """One witness-run group selling `amount` Defly (6 decimals; default 100) of the owner's balance,
    keeping the holding unless `close`; `tamper` edits it."""
    o = c['owner']['address']
    tiny = TinymanV2MainnetClient(algod_client=c['client'], user_address=o)
    legs, out = group.token_legs(tiny, o, TOKEN, amount, c["params"])
    legs = legs if close else legs[:2]                # the swap alone keeps the holding
    gain = out + (scan.SLOT if close else 0) - sum(t.fee for t in legs) - 4 * group.NETWORK_FEE
    fee = gain * fee_bps // 10_000
    txns = [group.create_guard(o, c['params'], c['fee_to'], tiny.validator_app_id), group.call(o, c['params'], c['app'], 'begin()void'),
            *legs, transaction.PaymentTxn(o, c['params'], c['fee_to'], fee)]
    if end:
        txns.append(group.call(o, c['params'], c['app'], 'end()uint64'))
    if tamper:
        tamper(txns, c)
    for t in txns:
        t.group = None
    failure, at, _ = group.simulate(c['client'], transaction.assign_group_id(txns), c['owner'].get('auth-addr'), c['round'])
    return failure, at, len(txns)


def test_honest_group_passes(chain):
    failure, _, _ = build(chain)
    assert failure is None


def test_fee_over_one_percent_fails_at_end(chain):
    failure, at, n = build(chain, fee_bps=200)
    assert failure and at == [n - 1]


def test_a_coin_to_the_operator_fails_at_end(chain):
    def coin(txns, c):
        txns.insert(-1, transaction.AssetTransferTxn(c['owner']['address'], c['params'], c['fee_to'], 1, TOKEN))
    failure, at, n = build(chain, tamper=coin)
    assert failure and at == [n - 1]


def test_a_rekey_fails_at_end(chain):
    def rekey(txns, c):
        txns[-1].rekey_to = c['fee_to']               # on end itself, so the guard's own check is what fails
    failure, at, n = build(chain, tamper=rekey)
    assert failure and at == [n - 1]


def test_a_payment_before_begin_fails_at_begin(chain):
    def early(txns, c):
        txns.insert(0, transaction.PaymentTxn(c['owner']['address'], c['params'], c['third'], 1_000))
    failure, at, _ = build(chain, tamper=early)
    assert failure and at == [2]


def test_a_payment_to_a_third_address_fails_at_end(chain):
    def third(txns, c):
        txns[-2].receiver = c['third']                 # the fee payment, redirected: no other check sees it
    failure, at, n = build(chain, tamper=third)
    assert failure and at == [n - 1]


def test_an_asset_configuration_fails_at_end(chain):
    def config(txns, c):
        txns.insert(-1, transaction.AssetConfigTxn(c['owner']['address'], c['params'], total=1, decimals=0,
                                                   default_frozen=False, unit_name='X', asset_name='X',
                                                   strict_empty_address_check=False))
    failure, at, n = build(chain, tamper=config)
    assert failure and at == [n - 1]


def test_a_call_to_another_app_fails_at_end(chain):
    always_yes = bytes([8, 0x81, 1])                   # TEAL v8 "pushint 1": an app that approves anything
    def other(txns, c):
        txns.insert(-1, transaction.ApplicationCreateTxn(
            c['owner']['address'], c['params'], transaction.OnComplete.NoOpOC, always_yes, always_yes,
            transaction.StateSchema(0, 0), transaction.StateSchema(0, 0)))
    failure, at, n = build(chain, tamper=other)
    assert failure and at == [n - 1]


def test_a_decoy_close_out_to_a_stranger_fails_at_begin(chain):
    """A zero transfer to a stranger, then a close-out of the whole balance to that stranger."""
    def decoy(txns, c):
        o, sp = c['owner']['address'], c['params']
        txns[2:-1] = [transaction.AssetTransferTxn(o, sp, c['third'], 0, TOKEN),
                      transaction.PaymentTxn(o, sp, c['fee_to'], 0),
                      transaction.AssetTransferTxn(o, sp, c['third'], 0, TOKEN, close_assets_to=c['third'])]
    failure, at, _ = build(chain, tamper=decoy)
    assert failure and at == [1]                      # begin: a "sale" of 0 is not the whole balance


def test_a_partial_sale_then_close_out_fails_at_begin(chain):
    """Sell 100 Defly, then close the rest out to the pool: the remainder would be given away."""
    def partial(txns, c):
        pool = txns[2].receiver
        txns.insert(4, transaction.AssetTransferTxn(c['owner']['address'], c['params'], pool, 0, TOKEN,
                                                    close_assets_to=pool))
    failure, at, _ = build(chain, tamper=partial)
    assert failure and at == [1]


def test_tinyman_refuses_a_sale_whose_tokens_go_to_a_stranger(chain):
    """The floor under the guard: Tinyman's swap must check its input went to the pool."""
    def stranger(txns, c):
        txns[2].receiver = c['third']
    failure, _, _ = build(chain, tamper=stranger)
    assert failure


def test_an_honest_whole_balance_sale_with_close_out_passes(chain):
    balance = next(a['amount'] for a in chain['owner']['assets'] if a['asset-id'] == TOKEN)
    failure, _, _ = build(chain, amount=balance, close=True)
    assert failure is None


def test_a_group_without_end_fails_at_begin(chain):
    failure, at, _ = build(chain, end=False)
    assert failure and at == [1]


def test_bytecode_sends_nothing_and_cannot_change():
    teal = (ARTIFACTS / 'DustGuard.approval.teal').read_text()
    assert 'itxn' not in teal                          # no inner transactions: it can move no coin
    assert 'txn OnCompletion\n    !\n    assert' in teal   # plain calls only: no update, delete, opt-in

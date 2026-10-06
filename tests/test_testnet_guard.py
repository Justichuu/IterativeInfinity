"""The deployed TestNet guard, with algod simulate: nothing is submitted.

Lifecycle: update, delete, opt-in and close-out are refused, even from the creator.

The token-swap attack: swap the owner's token X into token D through an X/D pool, sell the owner's original D
balance for ALGO, then close D out. The close-out gives away the D that X just bought, so X's value
is gone while every ALGO check passes. A sale must be into a token/ALGO pool, so the guard refuses.
Needs .env and testnet.json from deploy_testnet.py (with token2 and pool2); skips without them.

    .venv/Scripts/python.exe -m pytest tests/test_testnet_guard.py -q
"""
import json, sys
from pathlib import Path

import pytest
from algosdk import account, transaction
from algosdk.v2client import algod
from tinyman.assets import AssetAmount
from tinyman.v2.client import TinymanV2TestnetClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import group  # noqa: E402

STATE = ROOT / 'testnet.json'
ready = STATE.exists() and (ROOT / '.env').exists() and 'pool2' in json.loads(STATE.read_text())
pytestmark = pytest.mark.skipif(not ready, reason='needs a TestNet deploy with token2 and pool2')


@pytest.fixture(scope='module')
def net():
    import deploy_testnet as d
    s = json.loads(STATE.read_text())
    client = algod.AlgodClient('', d.ALGOD)
    me_key, seller_key = d.key_for('TESTNET_MNEMONIC'), d.key_for('TESTNET_SELLER_MNEMONIC')
    me, seller = account.address_from_private_key(me_key), account.address_from_private_key(seller_key)
    sp = client.suggested_params()
    held = {a['asset-id']: a['amount'] for a in client.account_info(seller).get('assets', [])}
    for token in (s['token'], s['token2']):                 # the seller holds both tokens; top up only below
        if token not in held:                                # 10,000, so other runs keep seeing one balance
            d.send(client, [transaction.AssetTransferTxn(seller, sp, seller, 0, token)], seller_key)
        if held.get(token, 0) < 10_000:
            d.send(client, [transaction.AssetTransferTxn(me, sp, seller, 10_000 - held.get(token, 0), token)], me_key)
    return dict(client=client, s=s, seller=seller, sp=client.suggested_params(),
                tiny=TinymanV2TestnetClient(algod_client=client, user_address=seller))


def swap(n, a, b, amount):
    """Tinyman's two transactions selling `amount` of asset a for asset b (0 is ALGO)."""
    pool = n['tiny'].fetch_pool(a, b)
    asset = pool.asset_1 if pool.asset_1.id == a else pool.asset_2
    quote = pool.fetch_fixed_input_swap_quote(AssetAmount(asset, amount), slippage=0.05)
    return pool.prepare_swap_transactions_from_quote(quote, user_address=n['seller'], suggested_params=n['sp']).transactions, pool


def run(n, legs):
    o, sp, app = n['seller'], n['sp'], n['s']['app']
    txns = [group.call(o, sp, app, 'begin()void'), *legs, group.call(o, sp, app, 'end()uint64')]
    for t in txns:
        t.group = None
    failure, at, _ = group.simulate(n['client'], transaction.assign_group_id(txns), None, None)
    return failure, at, len(txns)


def d_sale_and_close(n):
    """The honest part: sell the owner's whole D balance for ALGO, then close D out."""
    token = n['s']['token']
    balance = next(a['amount'] for a in n['client'].account_info(n['seller'])['assets'] if a['asset-id'] == token)
    legs, pool = swap(n, token, 0, balance)
    close = transaction.AssetTransferTxn(n['seller'], n['sp'], pool.address, 0, token, close_assets_to=pool.address)
    return [*legs, close]


def test_an_honest_sale_and_close_out_passes(net):
    failure, _, _ = run(net, d_sale_and_close(net))
    assert failure is None


def test_a_token_for_token_swap_before_a_close_out_is_refused(net):
    x_for_d, _ = swap(net, net['s']['token2'], net['s']['token'], 10_000)
    failure, at, n = run(net, [*x_for_d, *d_sale_and_close(net)])
    assert failure and at == [n - 1]                         # end: a sale must be into a token/ALGO pool


@pytest.mark.parametrize('action', ['update', 'delete', 'opt_in', 'close_out'])
def test_the_deployed_guard_refuses_lifecycle_calls_even_from_its_creator(net, action):
    """Tealer (Trail of Bits) flags is-updatable and is-deletable on this bytecode; the chain says no."""
    import deploy_testnet as d
    creator = account.address_from_private_key(d.key_for('TESTNET_MNEMONIC'))
    app, sp = net['s']['app'], net['sp']
    program = (group.ARTIFACTS / 'DustGuard.approval.bin').read_bytes()
    clear = (group.ARTIFACTS / 'DustGuard.clear.bin').read_bytes()
    txn = {'update': lambda: transaction.ApplicationUpdateTxn(creator, sp, app, program, clear),
           'delete': lambda: transaction.ApplicationDeleteTxn(creator, sp, app),
           'opt_in': lambda: transaction.ApplicationOptInTxn(creator, sp, app),
           'close_out': lambda: transaction.ApplicationCloseOutTxn(creator, sp, app)}[action]()
    failure, _, _ = group.simulate(net['client'], [txn], None, None)
    assert failure and 'logic eval error' in failure

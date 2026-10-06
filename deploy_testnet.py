"""Deploy DustGuard to TestNet and push one real sweep through it. TestNet ALGO has no value.

    python deploy_testnet.py FEE_ADDRESS

Keys come from .env (never committed): TESTNET_MNEMONIC funds and deploys; a second, seller key is
made on first run and added there. Each step's result goes to testnet.json, so a rerun resumes
where it stopped. Steps: the guard, a test token, the fee address opened on TestNet, a Tinyman pool
for the token with liquidity, a seller holding some of the token, and the sweep: the seller's own
signed group of begin, swap, close-out, fee and end, submitted and confirmed.
"""
import base64, json, sys
from pathlib import Path
from algosdk import account, mnemonic, transaction
from algosdk.v2client import algod
from tinyman.assets import AssetAmount
from tinyman.v2.client import TinymanV2TestnetClient

import group

HERE = Path(__file__).parent
STATE = HERE / 'testnet.json'
ENV = HERE / '.env'
ALGOD = 'https://testnet-api.algonode.cloud'
POOL_TOKENS, POOL_ALGO = 1_000_000, 5_000_000   # initial liquidity: one million test tokens against 5 ALGO
SELL = 100_000                                   # what the seller holds and sweeps


def env():
    return dict(line.split('=', 1) for line in ENV.read_text().splitlines() if '=' in line and not line.startswith('#'))


def key_for(name):
    """A TestNet key from .env, made and saved on first use."""
    values = env()
    if name not in values:
        k, _ = account.generate_account()
        values[name] = mnemonic.from_private_key(k)
        with ENV.open('a') as f:
            f.write(f'{name}={values[name]}\n')
    return mnemonic.to_private_key(values[name].strip())


def send(client, txns, key):
    """Sign every transaction with one key, submit, wait; returns the first confirmation."""
    if len(txns) > 1:
        txns = transaction.assign_group_id([setattr(t, 'group', None) or t for t in txns])
    txid = client.send_transactions([t.sign(key) for t in txns])
    return transaction.wait_for_confirmation(client, txid, 10) | {'txid': txid}


def step(state, name, fn):
    if name not in state:
        state[name] = fn()
        STATE.write_text(json.dumps(state, indent=1))
        print(f'{name}: {state[name]}')
    return state[name]


def main(fee_address):
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    client = algod.AlgodClient('', ALGOD)
    me_key, seller_key = key_for('TESTNET_MNEMONIC'), key_for('TESTNET_SELLER_MNEMONIC')
    me, seller = account.address_from_private_key(me_key), account.address_from_private_key(seller_key)
    tiny = TinymanV2TestnetClient(algod_client=client, user_address=me)
    sp = client.suggested_params()
    state['fee_address'], state['exchange'] = fee_address, tiny.validator_app_id

    app = step(state, 'app', lambda: send(client, [group.create_guard(me, sp, fee_address, tiny.validator_app_id)],
                                          me_key)['application-index'])
    token = step(state, 'token', lambda: send(client, [transaction.AssetConfigTxn(
        me, sp, total=10**9, decimals=0, default_frozen=False, unit_name='DUST', asset_name='DustBank test dust',
        strict_empty_address_check=False)], me_key)['asset-index'])
    step(state, 'fee_address_opened', lambda: client.account_info(fee_address)['amount'] >= 100_000 or bool(
        send(client, [transaction.PaymentTxn(me, sp, fee_address, 200_000)], me_key)))

    def tinyman_send(g):
        """A Tinyman group: our transactions signed with our key, the pool's own by its pool program."""
        g.sign_with_private_key(me, me_key)
        return tiny.submit(g, wait=True)

    def pool():
        p = tiny.fetch_pool(token, 0)
        tinyman_send(p.prepare_bootstrap_transactions(user_address=me, suggested_params=sp))
        p = tiny.fetch_pool(token, 0)
        tinyman_send(tiny.prepare_asset_optin_transactions(p.pool_token_asset.id, user_address=me))
        amounts = {p.asset_1: AssetAmount(p.asset_1, POOL_TOKENS), p.asset_2: AssetAmount(p.asset_2, POOL_ALGO)}
        tinyman_send(p.prepare_initial_add_liquidity_transactions(amounts, user_address=me, suggested_params=sp))
        return p.address
    step(state, 'pool', pool)

    def fund_seller():
        send(client, [transaction.PaymentTxn(me, sp, seller, 1_000_000)], me_key)
        send(client, [transaction.AssetTransferTxn(seller, sp, seller, 0, token)], seller_key)
        send(client, [transaction.AssetTransferTxn(me, sp, seller, SELL, token)], me_key)
        return seller
    step(state, 'seller', fund_seller)

    def sweep():
        seller_tiny = TinymanV2TestnetClient(algod_client=client, user_address=seller)
        txns, gain, fee = next(group.groups(seller_tiny, seller, fee_address, [(token, SELL)], sp, app,
                                            witness=False, holder=False))
        return {'txid': send(client, txns, seller_key)['txid'], 'estimated_gain': gain, 'fee': fee}
    step(state, 'sweep', sweep)

    totals = {k['key']: k['value'] for k in client.application_info(app)['params']['global-state']}
    print('guard totals:', {base64.b64decode(k).decode(): v.get('uint') for k, v in totals.items()
                            if v.get('type') == 2})


if __name__ == '__main__':
    main(sys.argv[1]) if len(sys.argv) == 2 else sys.exit(__doc__)

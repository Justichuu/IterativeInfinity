"""Build the unsigned groups that sell a wallet's dust, and test them with algod simulate.

    python group.py someone.algo      FEE_ADDRESS APP_ID   build and simulate against a deployed DustGuard
    python group.py someone.algo      FEE_ADDRESS new      witness run: DustGuard is created inside the
                                                            simulated group, so nothing is deployed

Each group the owner signs in their own wallet:
  begin                      DustGuard records what the owner can spend
  per sellable token:
    asset transfer of the whole balance to the Tinyman pool   } Tinyman V2 fixed-input swap,
    application call to Tinyman, which pays the ALGO back     } built by Tinyman's own SDK
    a zero transfer that closes the holding out, freeing 0.1 ALGO of minimum balance
  the 1% fee, one ALGO payment
  end                        DustGuard checks the fee, the gain and that no coin went to the operator
A group is all or nothing (dev.algorand.co, atomic groups). This file holds no key and submits nothing.
"""
import base64, json, sys, urllib.request
from pathlib import Path
from algosdk import abi, encoding, transaction
from algosdk.v2client import algod, models
from tinyman.assets import AssetAmount
from tinyman.v2.client import TinymanV2MainnetClient

import scan

FEE_BPS = 100            # 1% in basis points (hundredths of a percent); DustGuard enforces the same
SLIPPAGE = 0.01          # refuse a sale that would fill more than 1% under the quote
GROUP_MAX = 16           # transactions per atomic group, protocol limit
PER_TOKEN = 3            # swap transfer, swap call, close-out
NETWORK_FEE = 1_000      # microALGO, the minimum fee of one transaction
ARTIFACTS = Path(__file__).parent / 'contracts' / 'artifacts'


def fee_for(gain):
    """1% of what the owner gains, or nothing when it would not cover the transaction carrying it."""
    fee = gain * FEE_BPS // 10_000
    return fee if fee > NETWORK_FEE else 0


def call(owner, params, app_id, signature, args=()):
    selector = abi.Method.from_signature(signature).get_selector()
    return transaction.ApplicationNoOpTxn(owner, params, app_id, app_args=[selector, *args])


def create_guard(owner, params, fee_address, exchange):
    """DustGuard's creation, used only in a witness run so the contract is tested without deploying it."""
    selector = abi.Method.from_signature('create(address,uint64)void').get_selector()
    return transaction.ApplicationCreateTxn(
        owner, params, transaction.OnComplete.NoOpOC,
        (ARTIFACTS / 'DustGuard.approval.bin').read_bytes(), (ARTIFACTS / 'DustGuard.clear.bin').read_bytes(),
        transaction.StateSchema(5, 2), transaction.StateSchema(0, 0),
        app_args=[selector, encoding.decode_address(fee_address), exchange.to_bytes(8, 'big')])


def token_legs(client, owner, asset_id, amount, params):
    """Tinyman's swap of the whole balance for ALGO, then the close-out. Returns (txns, min ALGO out)."""
    pool = client.fetch_pool(asset_id, 0)
    asset = pool.asset_1 if pool.asset_1.id == asset_id else pool.asset_2
    quote = pool.fetch_fixed_input_swap_quote(AssetAmount(asset, amount), slippage=SLIPPAGE)
    swap = pool.prepare_swap_transactions_from_quote(quote, user_address=owner, suggested_params=params)
    close = transaction.AssetTransferTxn(owner, params, pool.address, 0, asset_id, close_assets_to=pool.address)
    return [*swap.transactions, close], quote.amount_out_with_slippage.amount


def groups(client, owner, fee_address, sells, params, app_id, witness):
    """Chunk the sales so begin, fee, end (and a witness run's create) fit the protocol limit."""
    overhead = 3 + witness
    per_group = (GROUP_MAX - overhead) // PER_TOKEN
    for start in range(0, len(sells), per_group):
        legs, gain = [], 0
        for asset_id, amount in sells[start:start + per_group]:
            try:
                txns, out = token_legs(client, owner, asset_id, amount, params)
            except Exception as e:                    # no pool, or none with liquidity: skip this token only
                print(f'skipped {asset_id}: {e}')
                continue
            legs += txns
            gain += out + scan.SLOT
        network = sum(t.fee for t in legs) + overhead * NETWORK_FEE   # the owner pays every fee in the group
        fee = fee_for(gain - network)
        pay = [transaction.PaymentTxn(owner, params, fee_address, fee, note=b'dustbank 1%')] if fee else []
        head = [create_guard(owner, params, fee_address, client.validator_app_id)] if witness else []
        txns = head + [call(owner, params, app_id, 'begin()void'), *legs, *pay, call(owner, params, app_id, 'end()uint64')]
        for t in txns:
            t.group = None
        yield transaction.assign_group_id(txns), gain - network, fee


def simulate(client, txns, signer, round_):
    """Run the unsigned group against the chain as of `round_`; nothing is submitted. `signer` is the
    account's auth address: a rekeyed account (signing handed to another key) is signed by that key."""
    unsigned = [transaction.SignedTransaction(t, None, authorizing_address=signer) for t in txns]
    req = models.SimulateRequest(txn_groups=[models.SimulateRequestTransactionGroup(txns=unsigned)],
                                 round=round_, allow_empty_signatures=True)
    g = client.simulate_transactions(req)['txn-groups'][0]
    return g.get('failure-message'), g.get('failed-at'), g


def measured(g):
    """The guard's own figures from end's global-state change: returned to the owner, and the fee."""
    delta = g['txn-results'][-1]['txn-result'].get('global-state-delta', [])
    vals = {base64.b64decode(d['key']).decode(): d['value'].get('uint', 0) for d in delta}
    return {k: vals.get(k, 0) for k in ('returned', 'fees')}


def next_app_id(round_):
    """The id a creation would get in the block after `round_`: that block's txn counter plus one."""
    url = f'{scan.ALGOD}/blocks/{round_}?header-only=true'
    return json.load(urllib.request.urlopen(url, timeout=20))['block']['tc'] + 1


def main(name, fee_address, app):
    sys.stdout.reconfigure(encoding='utf-8')
    client = algod.AlgodClient('', scan.ALGOD.rsplit('/v2', 1)[0])
    addrs = scan.addresses([name])
    rows, algo_usd = scan.scan(addrs)
    slot_usd = scan.SLOT / 1e6 * algo_usd
    sell_ids = {r['id'] for r in rows if scan.verdict(r, slot_usd) == 'sell'}
    params = client.suggested_params()
    witness = app == 'new'
    for owner in addrs:
        acct = client.account_info(owner)
        held = {h['asset-id']: h['amount'] for h in acct['assets']}
        sells = [(i, held[i]) for i in sorted(sell_ids) if held.get(i)]
        if not sells:
            continue
        tiny = TinymanV2MainnetClient(algod_client=client, user_address=owner)
        round_ = client.status()['last-round']
        app_id = next_app_id(round_) if witness else int(app)
        for txns, gain, fee in groups(tiny, owner, fee_address, sells, params, app_id, witness):
            failure, at, g = simulate(client, txns, acct.get('auth-addr'), round_)
            seen = measured(g) if not failure else None
            print(f'{len(txns):>2} txns  estimate: owner nets {(gain - fee) / 1e6:.6f} ALGO, fee {fee / 1e6:.6f}  '
                  f'simulate: {"OK" if not failure else f"FAILED at {at}: {failure}"}'
                  + (f'  guard measured: returned {seen["returned"] / 1e6:.6f} ALGO, fee {seen["fees"] / 1e6:.6f}'
                     if seen else ''))


if __name__ == '__main__':
    main(*sys.argv[1:4]) if len(sys.argv) == 4 else sys.exit(__doc__)

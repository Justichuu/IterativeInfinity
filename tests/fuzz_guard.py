"""Fuzz DustGuard: random groups from honest and thieving shapes, simulated on mainnet, judged by outcome.

    .venv/Scripts/python.exe tests/fuzz_guard.py TRIALS [APPROVAL.bin]     random groups
    .venv/Scripts/python.exe tests/fuzz_guard.py -3 [APPROVAL.bin]         every group of up to 3 shapes

Nothing is deployed, signed or submitted. Each trial creates the guard inside its own simulated group
(as group.py's witness run does), so any compiled approval program can be fuzzed, old or new.

The judge never reads the guard's rules. It looks only at what the group would do if it went through:
any ALGO leaving the owner except payments to the fee address, any token leaving except into its own
Tinyman pool in exchange for ALGO, any remainder given away by a close-out, any rekey, or fees over 1%
of the owner's gain. A "bad accept" is a group the guard lets through and the judge calls unsafe.

The exhaustive mode tries every sequence of up to K shapes from the library below: zero bad accepts
there covers that whole space, and nothing outside it. Zero bad accepts in N random trials bounds the
rate for THIS generator's groups at about 3 in N (95%), not for an attacker searching on purpose.
On 6 October, 800 random trials did not find the hole in the superseded guard; the exhaustive mode
is the one that has to.
"""
import copy, itertools, json, random, sys, time, urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from algosdk import encoding, transaction
from algosdk.v2client import algod
from tinyman.v2.client import TinymanV2MainnetClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import group, scan  # noqa: E402

TOKEN = 470842789                # Defly Token: has a Tinyman ALGO pool
OTHER = 31566704                 # USDC, for opt-in shapes
INDEXER = 'https://mainnet-idx.algonode.cloud/v2'
ALWAYS_YES = bytes([8, 0x81, 1])  # TEAL v8 "pushint 1"
FEE_BPS = 100


def get(url):
    return json.load(urllib.request.urlopen(url, timeout=20))


def setup():
    client = algod.AlgodClient('', scan.ALGOD.rsplit('/v2', 1)[0])
    holders = get(f'{INDEXER}/assets/{TOKEN}/balances?currency-greater-than=100000000&limit=40')['balances']
    accts = [client.account_info(h['address']) for h in holders]
    rich = [a for a in accts if a['amount'] - a['min-balance'] > 5_000_000]
    owner, fee_to, thief = rich[0], rich[1]['address'], rich[2]['address']
    o = owner['address']
    balance = next(a['amount'] for a in owner['assets'] if a['asset-id'] == TOKEN)
    sp = client.suggested_params()
    tiny = TinymanV2MainnetClient(algod_client=client, user_address=o)
    whole, _ = group.token_legs(tiny, o, TOKEN, balance, sp)
    part, _ = group.token_legs(tiny, o, TOKEN, balance // 2, sp)
    return dict(client=client, owner=o, auth=owner.get('auth-addr'), fee_to=fee_to, thief=thief,
                balance=balance, pool=whole[0].receiver, exchange=tiny.validator_app_id, sp=sp,
                whole=whole[:2], part=part[:2], opted_other=any(a['asset-id'] == OTHER for a in owner['assets']))


def shapes(c):
    """Named lists of transactions the generator draws from: honest and thieving."""
    o, sp, P, T, F = c['owner'], c['sp'], c['pool'], c['thief'], c['fee_to']
    axfer = lambda to, amt, close=None, asset=TOKEN: transaction.AssetTransferTxn(o, sp, to, amt, asset, close_assets_to=close)
    pay = lambda to, amt, close=None: transaction.PaymentTxn(o, sp, to, amt, close_remainder_to=close)
    return {
        'sale_whole': lambda: copy.deepcopy(c['whole']),
        'sale_part': lambda: copy.deepcopy(c['part']),
        'swap_only': lambda: [copy.deepcopy(c['whole'][1])],
        'close_to_pool': lambda: [axfer(P, 0, P)],
        'close_to_thief': lambda: [axfer(T, 0, T)],
        'zero_to_thief': lambda: [axfer(T, 0)],
        'one_to_thief': lambda: [axfer(T, 1)],
        'one_to_pool_no_swap': lambda: [axfer(P, 1)],
        'fee_small': lambda: [pay(F, 1_000)],
        'fee_big': lambda: [pay(F, 2_000_000)],
        'fee_zero': lambda: [pay(F, 0)],
        'algo_to_thief': lambda: [pay(T, 1_000)],
        'close_account_to_thief': lambda: [pay(F, 0, T)],
        'opt_in_other': lambda: [axfer(o, 0, asset=OTHER)],
        'other_app': lambda: [transaction.ApplicationCreateTxn(o, sp, transaction.OnComplete.NoOpOC, ALWAYS_YES,
                              ALWAYS_YES, transaction.StateSchema(0, 0), transaction.StateSchema(0, 0))],
        'asset_create': lambda: [transaction.AssetConfigTxn(o, sp, total=1, decimals=0, default_frozen=False,
                                 unit_name='X', asset_name='X', strict_empty_address_check=False)],
    }


def generate(rng, c, library, approval, clear, app_id, names=None):
    """A group around `names` (shapes in order); without `names`, the shapes and twists are random."""
    fixed = names is not None
    if fixed:
        names = list(names)
    elif rng.random() < 0.5:                                   # an honest sale with random shapes spliced in
        names = ['sale_whole', 'close_to_pool'] + (['fee_small'] if rng.random() < 0.5 else [])
        for _ in range(rng.randint(1, 2)):
            names.insert(rng.randint(0, len(names)), rng.choice(list(library)))
    else:                                                    # shapes drawn at random
        names = [rng.choice(list(library)) for _ in range(rng.randint(1, 5))]
    middle = [t for name in names for t in library[name]()]
    if not fixed and rng.random() < 0.15 and len(middle) > 1:              # shuffle two middle transactions
        i, j = rng.sample(range(len(middle)), 2)
        middle[i], middle[j] = middle[j], middle[i]
        names.append('swapped_order')
    if not fixed and rng.random() < 0.1:
        rng.choice(middle).rekey_to = c['thief']
        names.append('rekey')
    o, sp = c['owner'], c['sp']
    create = transaction.ApplicationCreateTxn(o, sp, transaction.OnComplete.NoOpOC, approval, clear,
                                              transaction.StateSchema(5, 2), transaction.StateSchema(0, 0),
                                              app_args=[group.abi.Method.from_signature('create(address,uint64)void').get_selector(),
                                                        encoding.decode_address(c['fee_to']), c['exchange'].to_bytes(8, 'big')])
    txns = [create, group.call(o, sp, app_id, 'begin()void'), *middle, group.call(o, sp, app_id, 'end()uint64')]
    if not fixed and rng.random() < 0.05:
        txns.pop()
        names.append('no_end')
    for t in txns:
        t.note = rng.randbytes(8)                           # identical shapes must not share a txid
        t.group = None
    return names, txns[:group.GROUP_MAX]


def paid_to(owner, inner):
    """ALGO an app's inner transactions paid the owner (simulate gives the receiver as an address)."""
    return sum(x['txn']['txn'].get('amt', 0) for x in inner
               if x['txn']['txn'].get('type') == 'pay' and x['txn']['txn'].get('rcv') == owner)


def judge(c, txns, result):
    """Outcome only: would this group, executed, take anything from the owner beyond a fee of at most 1%?"""
    o, P, F = c['owner'], c['pool'], c['fee_to']
    begin = next((i for i, t in enumerate(txns) if getattr(t, 'app_args', None) and t.app_args[:1] ==
                  [group.abi.Method.from_signature('begin()void').get_selector()]), 0)
    sent, fee, gain, why = 0, 0, 0, []
    held = {TOKEN: c['balance']}
    results = result['txn-results']
    for i, t in enumerate(txns):
        if getattr(t, 'rekey_to', None):
            why.append('rekey')
        if i > begin:
            gain -= t.fee
        if isinstance(t, transaction.PaymentTxn):
            if t.close_remainder_to:
                why.append('account closed to someone')
            if t.receiver == F:
                fee += t.amt
            elif t.amt:
                why.append('ALGO to someone else')
        elif isinstance(t, transaction.AssetTransferTxn):
            if t.receiver == o and t.amount == 0 and not t.close_assets_to:
                if t.index not in held:
                    gain -= scan.SLOT                       # a new opt-in locks 0.1 ALGO
                    held[t.index] = 0
                continue
            if t.amount:
                inner = results[i + 1]['txn-result'].get('inner-txns', []) if i + 1 < len(results) else []
                paid = paid_to(o, inner)
                if t.receiver != P or not paid:
                    why.append('token left without a sale')
                held[t.index] = held.get(t.index, 0) - t.amount
            if t.close_assets_to:
                if held.get(t.index, 0) > 0:
                    why.append('close-out gave a remainder away')
                gain += scan.SLOT
                held.pop(t.index, None)
        elif isinstance(t, transaction.ApplicationCallTxn):
            inner = results[i]['txn-result'].get('inner-txns', [])
            gain += paid_to(o, inner)
            if isinstance(t, transaction.ApplicationCreateTxn) and i > begin:
                gain -= scan.SLOT                           # an app the owner creates locks 0.1 ALGO
        elif isinstance(t, transaction.AssetConfigTxn) and i > begin:
            gain -= scan.SLOT                               # an asset the owner creates locks 0.1 ALGO
    if fee and fee * 10_000 > max(gain, 0) * FEE_BPS:
        why.append('fee over 1%')
    return why


def trial(c, library, approval, clear, seed, names=None):
    for attempt in range(5):
        try:
            round_ = c['client'].status()['last-round']
            names_, txns = generate(random.Random(seed), c, library, approval, clear, group.next_app_id(round_), names)
            transaction.assign_group_id(txns)
            failure, at, result = group.simulate(c['client'], txns, c['auth'], round_)
            accepted = failure is None
            why = judge(c, txns, result) if accepted else []
            reason = '' if accepted else failure.split('Details:')[0].split(': ', 2)[-1][:70] + f' at {at}'
            return dict(seed=seed, shapes=names_, accepted=accepted, unsafe=why, reason=reason)
        except Exception as e:                               # a stale round or a rate limit: wait and retry
            last = str(e)
            time.sleep(1 + attempt)
    return dict(seed=seed, error=last)


def main(n, approval_path=None):
    """n > 0: n random trials. n < 0: every sequence of 1 to -n shapes, exhaustively."""
    approval = Path(approval_path).read_bytes() if approval_path else (group.ARTIFACTS / 'DustGuard.approval.bin').read_bytes()
    clear = (group.ARTIFACTS / 'DustGuard.clear.bin').read_bytes()
    c = setup()
    library = shapes(c)
    if n > 0:
        jobs = [(s, None) for s in range(n)]
    else:
        jobs = [(s, seq) for s, seq in enumerate(seq for k in range(1, -n + 1)
                                                  for seq in itertools.product(library, repeat=k))]
        n = len(jobs)
    with ThreadPoolExecutor(4) as ex:
        runs = list(ex.map(lambda job: trial(c, library, approval, clear, *job), jobs))
    done = [r for r in runs if 'error' not in r]
    accepted = [r for r in done if r['accepted']]
    bad = [r for r in accepted if r['unsafe']]
    summary = {'trials': n, 'errors': n - len(done), 'accepted': len(accepted), 'accepted_safe': len(accepted) - len(bad),
               'bad_accepts': len(bad), 'bad_reasons': Counter(w for r in bad for w in r['unsafe']),
               'refusals': Counter(r['reason'].split(' pc=')[0] for r in done if not r['accepted']).most_common(6),
               'error_samples': [r['error'][:120] for r in runs if 'error' in r][:2],
               'bad_examples': [{'seed': r['seed'], 'shapes': r['shapes'], 'why': r['unsafe']} for r in bad[:5]],
               'bad_shapes': sorted(' + '.join(r['shapes']) for r in bad)}
    print(json.dumps(summary, indent=1, default=str))


if __name__ == '__main__':
    main(int(sys.argv[1]), *sys.argv[2:3]) if len(sys.argv) >= 2 else sys.exit(__doc__)

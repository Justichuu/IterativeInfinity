"""Read-only dust scan of an Algorand wallet: what each foreign token is worth,
what it locks, and which issuer powers it still carries.

    python scan.py someone.algo             an NFD (every verified address)
    python scan.py ADDRESS [ADDRESS ...]     or addresses

No keys, no signing, no transactions: public GET requests only (NFD, algonode,
Pera's public asset API). Shapes follow AATM/lib/scams.mjs: a scam is a shape
on chain and gets a face (1 seen, 0 measured absent, u not seen); a con is
intent and is always u, so no issuer is ever named a crook here.
"""
import json, sys, urllib.request, concurrent.futures as cf

SLOT = 100_000  # microALGO each asset holding adds to the minimum balance (dev.algorand.co, asset operations)
ALGOD = 'https://mainnet-api.algonode.cloud/v2'
PERA = 'https://mainnet.api.perawallet.app/v1/public/assets'


def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'dustbank-scan'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def addresses(args):
    out = []
    for a in args:
        out += get(f'https://api.nf.domains/nfd/{a}?view=brief')['caAlgo'] if a.endswith('.algo') else [a]
    return list(dict.fromkeys(out))


def shapes(params, pera):
    """Issuer powers, each 1 (present) or 0 (cleared, which is permanent). Warnings only: a sale in one
    signed group leaves nobody holding the token for a clawback or freeze to reach."""
    return {
        'clawback': int(bool(params.get('clawback'))),
        'freeze': int(bool(params.get('freeze'))),
        'default-frozen': int(bool(params.get('default-frozen'))),
        'pera-suspicious': int(pera.get('verification_tier') == 'suspicious'),
    }


def scan(addrs):
    accts = [get(f'{ALGOD}/accounts/{a}') for a in addrs]
    own = {c['index'] for d in accts for c in d.get('created-assets', [])}
    held, frozen = {}, set()
    for d in accts:
        for h in d.get('assets', []):
            if h['asset-id'] not in own:
                held[h['asset-id']] = held.get(h['asset-id'], 0) + h['amount']
                if h.get('is-frozen'):
                    frozen.add(h['asset-id'])  # this holding cannot move, so it cannot be sold or closed

    def one(i):
        pera, params = get(f'{PERA}/{i}/'), get(f'{ALGOD}/assets/{i}')['params']
        amount = held[i] / 10 ** params.get('decimals', 0)
        usd = float(pera['usd_value']) * amount if pera.get('usd_value') else None
        return {'id': i, 'name': pera.get('name') or params.get('name'), 'amount': amount, 'usd': usd,
                'tier': pera.get('verification_tier'), 'collectible': bool(pera.get('is_collectible')),
                'creator': params.get('creator'),
                'frozen': i in frozen, 'shapes': shapes(params, pera)}

    with cf.ThreadPoolExecutor(8) as ex:
        rows = list(ex.map(one, held))
    algo_usd = float(get(f'{PERA}/0/')['usd_value'])
    return rows, algo_usd


def verdict(r, slot_usd):
    if r['frozen']:
        return 'stuck'                       # the user's own holding is frozen; nothing can move it
    if r['usd'] is None:
        return 'u'                           # no price seen; not proof it is worthless
    return 'sell' if r['usd'] > slot_usd else 'dust'  # worth less than the 0.1 ALGO it locks


def main(argv):
    sys.stdout.reconfigure(encoding='utf-8')  # token names carry emoji; Windows consoles default to cp1252
    rows, algo_usd = scan(addresses(argv))
    slot_usd = SLOT / 1e6 * algo_usd
    for r in rows:
        r['verdict'] = verdict(r, slot_usd)
    rows.sort(key=lambda r: -(r['usd'] or 0))
    for r in rows:
        usd = f"{r['usd']:9.4f}" if r['usd'] is not None else '        u'
        hit = ','.join(k for k, v in r['shapes'].items() if v) or '-'
        print(f"{r['verdict']:<6} {usd} usd  {r['tier'] or 'u':<10} {hit:<30} {r['id']:>11}  {r['name']!s}")
    n, priced = len(rows), [r for r in rows if r['usd'] is not None]
    count = {v: sum(r['verdict'] == v for r in rows) for v in ('sell', 'dust', 'stuck', 'u')}
    print(f"\n{n} foreign tokens lock {n * SLOT / 1e6} ALGO ({n * slot_usd:.2f} usd at {algo_usd:.4f}).")
    print(f"priced {len(priced)} of {n}, worth {sum(r['usd'] for r in priced):.2f} usd; verdicts {count}; con: u")


if __name__ == '__main__':
    main(sys.argv[1:] or sys.exit(__doc__))

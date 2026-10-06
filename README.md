# DustBank

Sell the dust tokens in an Algorand wallet for ALGO, close each one out to free the 0.1 ALGO it
locks, and pay a 1% fee. Everything happens in groups the owner signs in their own wallet: all of a
group happens or none of it does. Nobody else holds the coins at any point.

**State: nothing is deployed.** The contract is tested against mainnet with algod `simulate`, which
runs a group against the live chain and submits nothing. Deploying it, and choosing the fee address,
are the owner's calls.

## The parts

| File | What it does |
|---|---|
| `scan.py` | Reads a wallet (NFD name or addresses): each foreign token's value, what it locks, issuer powers. Public data only. |
| `group.py` | Builds the unsigned sale groups (Tinyman swap, close-out, fee) and simulates them. Holds no key. |
| `contracts/dust_guard.py` | DustGuard, the on-chain referee. Its docstring says exactly what code enforces and what it does not. |
| `tests/test_guard.py` | An honest group must pass; each cheat, one change away, must fail at the guard. |

Verdicts from `scan.py`: `sell` (priced above the 0.1 ALGO it locks), `dust` (priced below it),
`stuck` (the owner's holding is frozen, so it cannot move: NFDs are like this), `u` (no price seen;
not proof it is worthless). Clawback, freeze and Pera's "suspicious" are shown, not refused: a sale
in one signed group leaves nobody holding the token for those powers to reach. A scam is a shape
on chain; intent is never judged here.

## Run it (Windows, PowerShell or Git Bash)

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python scan.py someone.algo
.venv\Scripts\python group.py someone.algo FEE_ADDRESS new
.venv\Scripts\python -m pytest tests -q
```

`group.py ... new` creates DustGuard inside each simulated group, so the contract is exercised
without being deployed. The tests need the network: they read mainnet through algonode and pick
public holders of a pooled token at run time.

Rebuild the contract after any edit, then rerun the tests:

```
.venv\Scripts\puyapy contracts\dust_guard.py --out-dir artifacts --output-bytecode
```

## Sources

- Atomic groups, asset opt-out and minimum balance: dev.algorand.co (concepts: transactions, assets).
- Swaps: Tinyman V2, through its own Python SDK.
- Token verification tiers and prices: Pera's public API, `mainnet.api.perawallet.app/v1/public/assets/{id}/`.

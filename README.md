<p align="center"><img src="logo.svg" width="160" alt="DustBank: a pyramid bank in the desert, its capstone an open eye"></p>

# Iterative Infinity: DustBank

Iterative Infinity was 500 layered generative NFTs on Algorand, by AllSeeingSkye: a foreground, a
middle and a background each, iterated in place through versions V3, V4, V5 and V5.1 on the same
tokens. A transparent art project with low prices and pieces that did something for their owners.

This is its revival, and DustBank is the first iteration. It sells the dust tokens in an Algorand
wallet for ALGO, closes each one out to free the 0.1 ALGO it locks, and takes a 1% fee that goes to
the address that created Iterative Infinity. **Holders of anything that address created pay no fee.**
Any artist can deploy their own guard with their own creator address and do the same for their
collectors.

Everything happens in groups the owner signs in their own wallet: all of a group happens or none of
it does, and nobody else ever holds the coins. An on-chain referee, DustGuard, checks every group.

**State: nothing is deployed.** The contract is tested against mainnet with algod `simulate`, which
runs a group against the live chain and submits nothing.

## What the referee enforces

DustGuard (`contracts/dust_guard.py`) runs at the start and the end of every group. In code:

- the fee is at most 1% of what the owner actually gained, measured on chain;
- the owner comes out ahead, or the whole group fails;
- every token sent goes into a Tinyman sale, and the only ALGO sent is the fee, so no coin can reach
  the operator or anyone else;
- no rekey, no asset reconfiguration, no calls to any other app;
- it holds nothing, sends nothing, and can never be updated or deleted;
- public totals, readable by anyone: iterations (one per refereed sale), ALGO returned, fees taken.

What it cannot enforce is written in its docstring: a page could build a sale without calling it.

## The parts

| File | What it does |
|---|---|
| `scan.py` | Reads a wallet (NFD name or addresses): each foreign token's value, what it locks, issuer powers. Public data only. |
| `group.py` | Builds the unsigned sale groups (Tinyman swap, close-out, fee) and simulates them. Holds no key. |
| `contracts/dust_guard.py` | DustGuard, the referee. |
| `tests/test_guard.py` | An honest group must pass; each cheat, one change away, must fail at the guard. |
| `logo.svg` | The bank. A pyramid, in the desert, with an eye on top. |

Verdicts from `scan.py`: `sell` (priced above the 0.1 ALGO it locks), `dust` (priced below it),
`stuck` (the owner's holding is frozen and cannot move; NFDs are like this), `u` (no price seen; not
proof it is worthless). Clawback, freeze and Pera's "suspicious" are shown, not refused: a sale in
one signed group leaves nobody holding the token for those powers to reach. A scam is a shape on
chain; intent is never judged here.

## Run it (Windows, PowerShell or Git Bash)

```text
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

```text
.venv\Scripts\puyapy contracts\dust_guard.py --out-dir artifacts --output-bytecode
```

## Sources

- Atomic groups, asset opt-out and minimum balance: dev.algorand.co (concepts: transactions, assets).
- Swaps: Tinyman V2, through its own Python SDK.
- Token verification tiers and prices: Pera's public API, `mainnet.api.perawallet.app/v1/public/assets/{id}/`.

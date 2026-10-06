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

**State: TestNet only.** DustGuard is deployed on TestNet as app 773799941, and real sweeps have gone
through it there. Its ALGO has no value. MainNet: not deployed. Signing in Pera has not been tried yet;
the page's code has been run end to end with a TestNet key signing in Pera's place.

## What the referee enforces

DustGuard (`contracts/dust_guard.py`) runs at the start and the end of every group. In code:

- the fee is at most 1% of what the owner actually gained, measured on chain;
- the owner never ends with less spendable ALGO, or the whole group fails;
- every token sent goes into a Tinyman swap, and a close-out must follow a sale of the whole balance,
  so nothing is given away; the only ALGO sent is the fee, so no coin can reach the operator or
  anyone else;
- no rekey, no clawback, no asset reconfiguration, no calls to any other app;
- it holds nothing, sends nothing, and can never be updated or deleted;
- public totals, readable by anyone: iterations (one per refereed sale), ALGO returned, fees taken.

What it cannot enforce is written in its docstring: the price (the guard counts ALGO, not what a
token is worth; the page refuses a sale more than 1% under Tinyman's quote, and the wallet shows each
minimum), and a page could build a sale without calling it.

## Every way value can leave your account, and what stops it

A group you sign can move value out of your account in only these ways. Each row names the guard's
check and the test that tries it. "Reasoned" means the check exists but no test has exercised it yet.

| Way out | The guard's check | Tried by |
|---|---|---|
| ALGO payment | only to the fee address, once, at most 1% of your gain | `test_fee_over_one_percent`, `test_a_payment_to_a_third_address`, fuzzer |
| Closing your account (payment close-to) | refused | fuzzer shape `close_account_to_thief` |
| Token transfer | must be followed by a Tinyman `swap`, which checks it went to the pool | `test_a_coin_to_the_operator`, `test_tinyman_refuses_a_sale_whose_tokens_go_to_a_stranger` |
| Token close-out | must follow a sale of your whole balance, to the same pool | `test_a_decoy_close_out`, `test_a_partial_sale_then_close_out` |
| Rekey (handing your signing to another key) | refused on every transaction | `test_a_rekey` |
| Clawback (moving someone else's tokens) | refused | reasoned |
| Other apps, or Tinyman methods other than swap | refused | `test_a_call_to_another_app`; non-swap Tinyman: reasoned |
| App opt-in, close-out, clear, update, delete | refused: plain calls only | bytecode check |
| Token settings, freeze, key registration | refused: only payments, transfers and plain calls | `test_an_asset_configuration`; freeze and key registration: reasoned |
| Anything before `begin` or after `end` | `begin` must be first, `end` last | `test_a_payment_before_begin`, `test_a_group_without_end` |

What no check covers: the price (the page refuses a sale more than 1% under Tinyman's quote, and the
wallet shows each minimum), and network fees, which a dishonest page could set high. They go to the
network, not to anyone, and the guard refuses the group only if they leave you with less ALGO.

The fee cap is arithmetic, not trust: the contract asserts `fee*10000 <= gain*100` on whole numbers,
and `gain/100 - fee = (gain*100 - fee*10000)/10000` (checked exactly by iDoMath), so the fee is at most
1% of the gain. A multiplication that overflows halts and fails the whole group (dev.algorand.co,
opcode reference); it cannot wrap around.

**Superseded: app 773797597 on TestNet.** It allowed a zero "sale" before a close-out, so a dishonest
page could have closed a whole balance out to any address, or sold part of a balance and given the
rest to the pool. Found on 6 October by reading the contract against its own claims, confirmed by
tests that fail on the old bytecode and pass on the new, and rediscovered by `tests/fuzz_guard.py`
without being told its shape. It held nothing and moved only TestNet coins.

**Fuzzer results, 6 October.** Every sequence of up to 3 shapes from the fuzzer's 16 (4,368 groups),
simulated on mainnet and judged by outcome alone:

| Contract | Accepted | Judged safe | Theft accepted |
|---|---|---|---|
| superseded (773797597's bytecode) | 137 | 125 | 12: both forms of the hole, found unaided |
| current | 124 | 124 | 0 |

That covers every group in that space and nothing outside it. 2,000 random groups on the current
contract also gave 0 thefts, but the same random mode missed the hole in the old one in 800 tries,
so it is weak evidence and the exhaustive mode is the one that counts.

## The parts

| File | What it does |
|---|---|
| `index.html` | The signing page: look up a wallet, see every step in plain words, connect Pera and sign. |
| `sweep.js` | The page's logic: quotes on Tinyman, builds the groups. Holds no key. |
| `scan.py` | Reads a wallet (NFD name or addresses): each foreign token's value, what it locks, issuer powers. Public data only. |
| `group.py` | Builds the unsigned sale groups (Tinyman swap, close-out, fee) and simulates them. Holds no key. |
| `contracts/dust_guard.py` | DustGuard, the referee. |
| `tests/test_guard.py` | An honest group must pass; each cheat, one change away, must fail at the guard. |
| `tests/page.test.mjs` | The page's logic sells a TestNet token through the deployed guard, for real, and a 2% fee is refused. |
| `tests/fuzz_guard.py` | Random groups, honest and thieving, simulated on mainnet and judged by outcome alone. |
| `deploy_testnet.py` | Deploys the guard to TestNet with a test token, pool and seller, and runs one sweep. |
| `logo.svg` | The bank. A pyramid, in the desert, with an eye on top. |

Verdicts from `scan.py`: `sell` (priced above the 0.1 ALGO it locks), `dust` (priced below it),
`stuck` (the owner's holding is frozen and cannot move; NFDs are like this), `u` (no price seen; not
proof it is worthless). Clawback, freeze and Pera's "suspicious" are shown, not refused: a sale in
one signed group leaves nobody holding the token for those powers to reach. A scam is a shape on
chain; intent is never judged here.

## Run it (Windows, PowerShell or Git Bash)

The page needs no build. Serve the folder and open it:

```text
python -m http.server 8000
```

Then open http://localhost:8000. To check Python and the contract:

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

The page's own test needs Node, a TestNet deploy and its keys in `.env` (never committed):

```text
npm install
.venv\Scripts\python deploy_testnet.py FEE_ADDRESS
node --test tests/page.test.mjs
```

Rebuild the contract after any edit, then rerun the tests:

```text
.venv\Scripts\puyapy contracts\dust_guard.py --out-dir artifacts --output-bytecode
```

## Sources

- Atomic groups, asset opt-out and minimum balance: dev.algorand.co (concepts: transactions, assets).
- Swaps: Tinyman V2, through its own Python SDK.
- Token verification tiers and prices: Pera's public API, `mainnet.api.perawallet.app/v1/public/assets/{id}/`.

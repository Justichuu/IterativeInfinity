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

**State: TestNet only.** DustGuard is deployed on TestNet as app 773802502, and real sweeps have gone
through it there. Its ALGO has no value. MainNet: not deployed. Signing in Pera has not been tried yet;
the page's code has been run end to end with a TestNet key signing in Pera's place.

## What the referee enforces

DustGuard (`contracts/dust_guard.py`) runs at the start and the end of every group. In code:

- the fee is at most 1% of what the owner actually gained, measured on chain;
- the owner never ends with less spendable ALGO, or the whole group fails;
- every token sent goes into a Tinyman swap for ALGO, through a pool whose own Tinyman record pairs
  that token with ALGO, and a close-out must follow a sale of the whole balance, so nothing is given
  away; the only ALGO sent is the fee, so no coin can reach the operator or anyone else;
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
| Closing your account (payment close-to) | refused; the network itself also refuses while any token is held | fuzzer shape `close_account_to_thief` (refused by the network first; the guard's check is reasoned) |
| Token transfer | must go to a pool whose Tinyman record pairs that token with ALGO, followed by a `swap` | `test_a_coin_to_the_operator`, `test_tinyman_refuses_a_sale_whose_tokens_go_to_a_stranger`, `test_a_token_for_token_swap_before_a_close_out_is_refused` |
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

## Known Algorand flaws, checked against DustGuard

Researched 6 October from people who audit and attack Algorand contracts: Trail of Bits' *Not So
Smart Contracts* catalogue and their analyzer Tealer, the Algorand developer team's *Smart Contract
Security Best Practices*, the Panda paper (USENIX Security 2023, which measured these flaws across
deployed contracts), and the Tinyman (January 2022) and MyAlgo (February 2023) incidents.

| Flaw (source) | DustGuard |
|---|---|
| Rekeying (Trail of Bits; Panda) | refused on every transaction in the group; tested |
| Closing an account (Trail of Bits; Panda) | refused; the network also refuses while any token is held |
| Closing an asset (Trail of Bits; Panda) | close-out only after a sale of the whole balance into that token's ALGO pool; three attack tests |
| Unchecked payment or asset receiver (Panda) | payments only to the fee address; tokens only to a pool whose Tinyman record pairs them with ALGO; tested |
| Asset id check (Trail of Bits) | the sale's token must match the pool's record and the close-out's token; tested |
| Update or delete by anyone (Trail of Bits; Panda; Tealer) | refused, even from the creator, on the deployed TestNet app; tested |
| Clear state and other OnComplete values (Trail of Bits) | plain calls only, for the guard and every app call in the group; it keeps no local state |
| Group size and absolute indexes (Trail of Bits; Tealer) | the guard reads every transaction in the group and pins begin first and end last; Tealer flags it because it cannot follow the loop; reasoned |
| Inner transaction fees, denial of service by opt-out (Trail of Bits) | not applicable: the guard has no inner transactions and holds nothing |
| Time-based replay and leases (Trail of Bits) | not applicable: no periodic payments; a signed group can only land once |
| Overflow and underflow (Algorand guide) | the AVM halts instead of wrapping, so the group fails |
| Unchecked transaction fees (Trail of Bits; Panda) | not covered: a dishonest page could set high network fees; they go to the network, and the guard only refuses if you end with less ALGO |
| A bug in the exchange itself (Tinyman, January 2022: a flaw in Tinyman's first version let attackers withdraw one side of a pool) | not covered: DustGuard trusts Tinyman's current exchange to pay what its swap promises |
| A tampered page (MyAlgo, February 2023: malicious code injected through the wallet's content network drained 25 wallets) | not covered: this page loads its libraries from esm.sh at run time; a tampered copy could build a group without the guard. Pera shows every transaction before signing |

Tealer 0.1.2 run on the compiled guard: no findings for closing accounts or assets. It also flagged
updatable, deletable, rekey, fee and group-size paths; it could not parse one compiler line, its rekey
and fee detectors are written for logic signatures, and the chain refuses update and delete (tested).

**Superseded: app 773799941 on TestNet.** It let a sale be a token-for-token swap. A dishonest page
could swap token X into token D, sell the owner's original D, and close D out, giving away what X
bought; or swap X into a worthless token through a pool it controls. Found on 6 October by a second
review of the guard against its claims, confirmed on TestNet (`tests/test_testnet_guard.py` passed
the attack against it), and rediscovered unaided by the fuzzer's TestNet mode. Now a sale must go
into a pool whose own Tinyman record pairs the token with ALGO.

**Superseded: app 773797597 on TestNet.** It allowed a zero "sale" before a close-out, so a dishonest
page could have closed a whole balance out to any address, or sold part of a balance and given the
rest to the pool. Found on 6 October by reading the contract against its own claims, confirmed by
tests that fail on the old bytecode and pass on the new, and rediscovered by `tests/fuzz_guard.py`
without being told its shape. It held nothing and moved only TestNet coins.

**Fuzzer results, 6 October.** Every sequence of up to 3 shapes from the fuzzer's 16 (4,368 groups),
simulated on mainnet and judged by outcome alone:

| Network, shapes | Contract | Groups | Accepted | Judged safe | Unsafe accepted |
|---|---|---|---|---|---|
| mainnet, 16 | 773797597's bytecode | 4,368 | 137 | 125 | 12: both forms of the first hole, found unaided |
| TestNet, 17 (adds a token-for-token swap) | 773799941's bytecode | 5,219 | 118 | 77 | 41: every one uses a token-for-token swap, one of them the full theft; found unaided |
| mainnet, 16 | current | 4,368 | 124 | 124 | 0 |
| TestNet, 17 | current | 5,219 | 77 | 77 | 0 |

The current contract accepts exactly the safe groups the superseded one did on TestNet (77 of 77), so
the fixes cost honest sales nothing. Each row covers every group of up to 3 shapes in its space and
nothing outside it: one or two tokens, one ALGO pool, no groups of 4 or more shapes. 2,000 random groups on the current
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
| `tests/fuzz_guard.py` | Random or exhaustive groups, honest and thieving, simulated and judged by outcome alone. |
| `tests/test_testnet_guard.py` | On TestNet: a token-for-token swap before a close-out is refused. |
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

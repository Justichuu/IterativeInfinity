// The signing page's logic: read a wallet, quote each token on Tinyman, build the groups DustGuard
// referees. The page (index.html) and the node test (tests/page.test.mjs) both import this file.
// It holds no key and submits nothing; signing is the wallet's job.
//
// Each group: begin, then per token a Tinyman sale and a close-out (frees the 0.1 ALGO the holding
// locks), then the fee, then end. A group is all or nothing (dev.algorand.co, atomic groups).
import algosdk from 'algosdk';
import { poolUtils, Swap, SwapQuoteType, SwapType, getValidatorAppID } from '@tinymanorg/tinyman-js-sdk';

export const NETWORKS = {
  testnet: { algod: 'https://testnet-api.algonode.cloud', app: 773799941,   // 773797597 superseded, see README
             chainId: 416002,
             explorer: 'https://lora.algokit.io/testnet' },
  mainnet: { algod: 'https://mainnet-api.algonode.cloud', app: 0, chainId: 416001,      // 0: not deployed
             explorer: 'https://lora.algokit.io/mainnet' },
};
export const FEE_BPS = 100n;     // 1% in basis points (hundredths of a percent); DustGuard enforces the same
const SLIPPAGE = 0.01;           // refuse a sale that fills more than 1% under the quote
const SLOT = 100_000n;           // microALGO each asset holding locks in minimum balance
const MIN_FEE = 1_000n;          // microALGO, the minimum fee of one transaction
const PER_GROUP = 4;             // tokens per group: 3 transactions each, plus begin, fee, end, within 16

const getJSON = async url => {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} from ${url}`);
  return r.json();
};

/** DustGuard's public state: fee address, exchange, and the running totals. */
export async function guardState(net) {
  const app = await getJSON(`${net.algod}/v2/applications/${net.app}`);
  const out = {};
  for (const { key, value } of app.params['global-state'] || []) {
    const name = atob(key);
    out[name] = value.type === 1 ? algosdk.encodeAddress(Uint8Array.from(atob(value.bytes), c => c.charCodeAt(0)))
                                 : BigInt(value.uint);
  }
  return out;
}

/** An NFD name (something.algo) to its first verified address; an address passes through. */
export async function resolve(name) {
  if (!name.endsWith('.algo')) return name;
  return (await getJSON(`https://api.nf.domains/nfd/${name}?view=brief`)).caAlgo[0];
}

const call = (sender, appIndex, signature, suggestedParams) => algosdk.makeApplicationNoOpTxnFromObject(
  { sender, appIndex, suggestedParams, appArgs: [algosdk.ABIMethod.fromSignature(signature).getSelector()] });

/** Quote every foreign, unfrozen token the address holds; build the groups. */
export async function plan(network, address) {
  const net = NETWORKS[network];
  if (!net.app) throw new Error(`DustGuard is not deployed on ${network}`);
  const client = new algosdk.Algodv2('', net.algod, '');
  const guard = await guardState(net);
  if (Number(guard.exchange) !== getValidatorAppID(network, 'v2')) throw new Error('guard and Tinyman disagree');
  const acct = await getJSON(`${net.algod}/v2/accounts/${address}`);
  const feeAcct = await getJSON(`${net.algod}/v2/accounts/${guard.fee_address}`);
  const own = new Set((acct['created-assets'] || []).map(a => a.index));
  const artist = new Set((feeAcct['created-assets'] || []).map(a => a.index));
  const holder = (acct.assets || []).some(h => artist.has(h['asset-id']) && h.amount > 0);

  const rows = [];
  for (const h of acct.assets || []) {
    const id = h['asset-id'];
    if (own.has(id) || h.amount === 0) continue;
    const p = (await getJSON(`${net.algod}/v2/assets/${id}`)).params;
    const row = { id, name: p.name || p['unit-name'] || String(id), unit: p['unit-name'] || '',
                  amount: BigInt(h.amount), decimals: p.decimals };
    rows.push(row);
    if (h['is-frozen']) { row.status = 'frozen: cannot move'; continue; }
    const pool = await poolUtils.v2.getPoolInfo({ client, network, asset1ID: id, asset2ID: 0 });
    if (!poolUtils.v2.isPoolReady(pool)) { row.status = 'no Tinyman pool'; continue; }
    const quote = Swap.v2.getFixedInputDirectSwapQuote({ pool, amount: row.amount,
      assetIn: { id, decimals: p.decimals }, assetOut: { id: 0, decimals: 6 } });
    Object.assign(row, { status: 'sell', pool, quote, out: BigInt(quote.assetOutAmount),
                         minOut: BigInt(quote.assetOutAmount) * 99n / 100n });
  }

  const sp = { ...(await client.getTransactionParams().do()), flatFee: true, fee: MIN_FEE };
  const sells = rows.filter(r => r.status === 'sell');
  const groups = [];
  for (let i = 0; i < sells.length; i += PER_GROUP) {
    const legs = [];
    let gain = 0n;
    for (const r of sells.slice(i, i + PER_GROUP)) {
      const swap = await Swap.v2.generateTxns({ client, network, initiatorAddr: address, slippage: SLIPPAGE,
        swapType: SwapType.FixedInput, quote: { type: SwapQuoteType.Direct, data: { quote: r.quote, pool: r.pool } } });
      const poolAddress = r.pool.account.address().toString();
      const close = algosdk.makeAssetTransferTxnWithSuggestedParamsFromObject({ sender: address, receiver: poolAddress,
        closeRemainderTo: poolAddress, amount: 0, assetIndex: r.id, suggestedParams: sp });
      legs.push(...swap.map(s => s.txn), close);
      gain += r.minOut + SLOT;
    }
    const network_fees = legs.reduce((s, t) => s + BigInt(t.fee), 0n) + 3n * MIN_FEE;   // + begin, fee, end
    const net_gain = gain - network_fees;
    const due = net_gain * FEE_BPS / 10_000n;
    const fee = holder || due <= MIN_FEE ? 0n : due;
    const pay = fee ? [algosdk.makePaymentTxnWithSuggestedParamsFromObject({ sender: address,
      receiver: guard.fee_address, amount: fee, suggestedParams: sp, note: new TextEncoder().encode('dustbank 1%') })] : [];
    const txns = [call(address, net.app, 'begin()void', sp), ...legs, ...pay, call(address, net.app, 'end()uint64', sp)];
    for (const t of txns) t.group = undefined;     // Tinyman's legs arrive grouped; regroup them with ours
    groups.push({ txns: algosdk.assignGroupID(txns), gain: net_gain, fee });
  }
  return { guard, holder, rows, groups };
}

/** Submit signed groups one after another; returns each group's first transaction id. */
export async function submit(network, signedGroups) {
  const client = new algosdk.Algodv2('', NETWORKS[network].algod, '');
  const ids = [];
  for (const signed of signedGroups) {
    const { txid } = await client.sendRawTransaction(signed).do();
    await algosdk.waitForConfirmation(client, txid, 10);
    ids.push(txid);
  }
  return ids;
}

/** microALGO to a readable ALGO string. */
export const algo = micro => (Number(micro) / 1e6).toFixed(6);

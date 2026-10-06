// The signing page's logic on TestNet, for real: the same sweep.js the page loads plans a sale, the
// TestNet seller key signs it in place of a wallet, and the chain confirms it. TestNet ALGO has no value.
// Needs .env and testnet.json from deploy_testnet.py; skips without them.
//
//   node --test tests/page.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import algosdk from 'algosdk';
import { NETWORKS, guardState, plan, submit } from '../sweep.js';

const root = new URL('..', import.meta.url);
const ready = existsSync(new URL('.env', root)) && existsSync(new URL('testnet.json', root));
const env = ready ? Object.fromEntries(readFileSync(new URL('.env', root), 'utf8').split('\n')
  .filter(l => l.includes('=') && !l.startsWith('#')).map(l => [l.slice(0, l.indexOf('=')), l.slice(l.indexOf('=') + 1).trim()])) : {};
const state = ready ? JSON.parse(readFileSync(new URL('testnet.json', root), 'utf8')) : {};
const net = NETWORKS.testnet;
const client = new algosdk.Algodv2('', net.algod, '');
const balance = async a => BigInt((await client.accountInformation(a).do()).amount);

async function send(txns, sk) {
  if (txns.length > 1) algosdk.assignGroupID(txns);
  const { txid } = await client.sendRawTransaction(txns.map(t => t.signTxn(sk))).do();
  await algosdk.waitForConfirmation(client, txid, 10);
}

test('the page logic sells a TestNet token through DustGuard and the chain agrees', { skip: !ready }, async () => {
  const funder = algosdk.mnemonicToSecretKey(env.TESTNET_MNEMONIC);
  const seller = algosdk.mnemonicToSecretKey(env.TESTNET_SELLER_MNEMONIC);
  const me = funder.addr.toString(), you = seller.addr.toString(), token = state.token;
  const sp = await client.getTransactionParams().do();
  const held = (await client.accountInformation(you).do()).assets?.some(a => Number(a.assetId) === token);
  if (!held) await send([algosdk.makeAssetTransferTxnWithSuggestedParamsFromObject(
    { sender: you, receiver: you, amount: 0, assetIndex: token, suggestedParams: sp })], seller.sk);
  await send([algosdk.makeAssetTransferTxnWithSuggestedParamsFromObject(
    { sender: me, receiver: you, amount: 100_000, assetIndex: token, suggestedParams: sp })], funder.sk);

  const before = await guardState(net);
  const feeBefore = await balance(before.fee_address);
  const { rows, groups } = await plan('testnet', you);
  assert.equal(rows.find(r => r.id === token).status, 'sell');
  assert.equal(groups.length, 1);
  const [{ txns, gain, fee }] = groups;
  assert.ok(fee > 0n && fee * 10_000n <= gain * 100n, `fee ${fee} is at most 1% of ${gain}`);

  await submit('testnet', [txns.map(t => t.signTxn(seller.sk))]);

  const after = await guardState(net);
  assert.equal(after.iterations, before.iterations + 1n);
  assert.equal(after.fees - before.fees, fee);
  assert.equal(await balance(before.fee_address) - feeBefore, fee);
  const still = (await client.accountInformation(you).do()).assets?.some(a => Number(a.assetId) === token);
  assert.equal(still, false, 'the holding is closed out');
});

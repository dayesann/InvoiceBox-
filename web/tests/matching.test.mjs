import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveAssociations } from '../src/matching.js';

const expense = (id, amount = 10, extra = {}) => ({ id, category: '公共交通', amount, original_name: `${id}.pdf`, recognized_text: '城市地铁服务', ...extra });
const trip = (id, amount = 10, extra = {}) => ({ id, category: '行程单', amount, original_name: `${id}.pdf`, recognized_text: '地铁乘车行程单', ...extra });
const get = (records, id) => records.find(row => row.id === id);

test('unrenamed generic files yield a suggestion, never automatic confirmation', () => {
  const result = resolveAssociations([expense('invoice'), trip('trip')]);
  assert.equal(get(result, 'trip').parent_id, 'invoice');
  assert.equal(get(result, 'trip').association_state, 'suggested');
  assert.match(get(result, 'trip').association_reason, /金额一致/);
});

test('same amount is not enough to choose among multiple invoices and order does not decide', () => {
  const input = [expense('a', 5), expense('b', 5), trip('c', 5), trip('d', 5)];
  for (const records of [input, [...input].reverse()]) {
    const result = resolveAssociations(records);
    for (const id of ['c', 'd']) {
      assert.equal(get(result, id).parent_id, '');
      assert.equal(get(result, id).association_state, 'unmatched');
      assert.deepEqual(get(result, id).association_candidates, ['a', 'b']);
    }
  }
});

test('amount or transport-type conflicts cannot be suggested', () => {
  assert.equal(get(resolveAssociations([expense('a', 10), trip('b', 11)]), 'b').parent_id, '');
  assert.equal(get(resolveAssociations([expense('a', 10), trip('b', 10, { recognized_text: '网约车行程单' })]), 'b').parent_id, '');
  assert.equal(get(resolveAssociations([expense('a', 10, { category: '打车费' }), trip('b')]), 'b').parent_id, '');
});

test('original batch name disambiguates same amounts without trusting display name', () => {
  const input = [
    expense('a', 5, { original_name: '甲-发票.pdf' }), expense('b', 5, { original_name: '乙-发票.pdf' }),
    trip('c', 5, { original_name: '甲-附单01.pdf' }), trip('d', 5, { original_name: '乙-行程单.pdf' })
  ];
  const result = resolveAssociations(input);
  assert.equal(get(result, 'c').parent_id, 'a');
  assert.equal(get(result, 'd').parent_id, 'b');
  const renamed = input.map(item => ({ ...item, display_name: '无关的新名字.pdf', file: { name: item.original_name } }));
  assert.equal(get(resolveAssociations(renamed), 'c').parent_id, 'a');
  assert.equal(get(resolveAssociations(renamed.map(({ file, ...rest }) => rest)), 'c').parent_id, 'a');
});

test('generic original filename indexes can disambiguate same-amount candidates', () => {
  const input = [
    expense('a', 5, { original_name: 'invoice.pdf' }), expense('b', 5, { original_name: '1-invoice.pdf' }),
    trip('c', 5, { original_name: 'trip.pdf' }), trip('d', 5, { original_name: '1-trip.pdf' })
  ];
  for (const records of [input, [...input].reverse()]) {
    const result = resolveAssociations(records);
    assert.equal(get(result, 'c').parent_id, 'a');
    assert.equal(get(result, 'd').parent_id, 'b');
    assert.equal(get(result, 'c').association_state, 'suggested');
    assert.match(get(result, 'd').association_reason, /原文件序号一致/);
  }
});

test('an extracted date can rank equal-amount candidates but cannot override an amount conflict', () => {
  const input = [expense('a', 5, { date_start: '2026-11-01' }), expense('b', 5, { date_start: '2026-11-02' }), trip('c', 5, { date_start: '2026-11-02' })];
  const result = resolveAssociations(input);
  assert.equal(get(result, 'c').parent_id, 'b');
  assert.equal(get(result, 'c').association_state, 'suggested');
  assert.match(get(result, 'c').association_reason, /核对含义/);
  assert.equal(get(resolveAssociations([expense('a', 7, { date_start: '2026-11-02' }), trip('c', 5, { date_start: '2026-11-02' })]), 'c').parent_id, '');
});

test('a shared route ranks equal candidates when both records actually have route data', () => {
  const input = [expense('a', 5, { route: '上海 → 苏州' }), expense('b', 5, { route: '苏州 → 上海' }), trip('c', 5, { route: '上海→苏州' })];
  const result = resolveAssociations(input);
  assert.equal(get(result, 'c').parent_id, 'a');
  assert.match(get(result, 'c').association_reason, /路线一致/);
});

test('user confirmation persists across sorting, edits and multiple attachments', () => {
  const input = [expense('a'), expense('b'), trip('c', 11, { parent_id: 'a', association_origin: 'manual' }), trip('d', 10, { parent_id: 'a', association_origin: 'manual' })];
  const result = resolveAssociations(input.reverse());
  assert.equal(get(result, 'c').parent_id, 'a');
  assert.equal(get(result, 'd').parent_id, 'a');
  assert.equal(get(result, 'c').association_state, 'confirmed');
  assert.match(get(result, 'c').association_warning, /金额不一致/);
});

test('explicitly unlinked attachments stay unlinked; empty automatic IDs can rematch', () => {
  const rejected = trip('b', 10, { parent_id: '', association_origin: 'rejected' });
  assert.equal(get(resolveAssociations([expense('a'), rejected]), 'b').parent_id, '');
  const reset = { ...rejected, association_origin: 'auto' };
  assert.equal(get(resolveAssociations([expense('a'), reset]), 'b').parent_id, 'a');
  assert.equal(get(resolveAssociations([expense('a'), trip('b', 10, { parent_id: '' })]), 'b').parent_id, 'a');
});

test('missing parents and changing candidates trigger a fresh decision', () => {
  const stale = trip('c', 10, { parent_id: 'deleted', association_origin: 'manual' });
  assert.equal(get(resolveAssociations([expense('a'), stale]), 'c').parent_id, 'a');
  const first = get(resolveAssociations([expense('a'), trip('c')]), 'c');
  assert.equal(first.parent_id, 'a');
  const later = get(resolveAssociations([expense('a'), expense('b'), first]), 'c');
  assert.equal(later.parent_id, '');
  assert.equal(later.association_state, 'unmatched');
});

test('an occupied manual parent is not claimed by an automatic suggestion', () => {
  const result = resolveAssociations([expense('a'), trip('b', 10, { parent_id: 'a', association_origin: 'manual' }), trip('c')]);
  assert.equal(get(result, 'c').parent_id, '');
});

test('manually adding a second attachment does not displace an existing automatic link', () => {
  const initial = resolveAssociations([expense('a'), trip('b')]);
  assert.equal(get(initial, 'b').parent_id, 'a');
  const result = resolveAssociations([...initial, trip('c', 10, { parent_id: 'a', association_origin: 'manual' })]);
  assert.equal(get(result, 'b').parent_id, 'a');
  assert.equal(get(result, 'b').association_state, 'suggested');
  assert.equal(get(result, 'c').parent_id, 'a');
  assert.equal(get(result, 'c').association_state, 'confirmed');
});

test('manual association to a nontransport expense requires extra review', () => {
  const result = resolveAssociations([expense('a', 10, { category: '住宿', recognized_text: '酒店住宿' }), trip('b', 10, { parent_id: 'a', association_origin: 'manual' })]);
  assert.match(get(result, 'b').association_warning, /费用类别可能不匹配/);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { planCardDrop } from '../src/drag-association.js';
const invoice = id => ({ id, category: '公共交通' });
const trip = (id, parent_id = '') => ({ id, category: '行程单', parent_id });
const ids = result => result.records.map(item => item.id);

test('dropping a trip onto an invoice creates a link and places it adjacent', () => {
  const result = planCardDrop([invoice('a'), invoice('b'), trip('c')], 'c', 'a');
  assert.equal(result.attachmentId, 'c');
  assert.equal(result.expenseId, 'a');
  assert.deepEqual(ids(result), ['a', 'c', 'b']);
});

test('dropping an invoice onto a trip also creates a link', () => {
  const result = planCardDrop([invoice('a'), invoice('b'), trip('c')], 'a', 'c');
  assert.equal(result.attachmentId, 'c');
  assert.equal(result.expenseId, 'a');
  assert.deepEqual(ids(result), ['b', 'a', 'c']);
});

test('adding another attachment preserves the existing one-to-many group', () => {
  const result = planCardDrop([invoice('a'), trip('b', 'a'), invoice('c'), trip('d')], 'd', 'a');
  assert.deepEqual(ids(result), ['a', 'b', 'd', 'c']);
});

test('same-role drops only reorder and invalid drops do nothing', () => {
  const records = [invoice('a'), invoice('b'), trip('c')];
  assert.deepEqual(ids(planCardDrop(records, 'a', 'b')), ['b', 'a', 'c']);
  assert.equal(planCardDrop(records, 'a', 'b').attachmentId, '');
  assert.equal(planCardDrop(records, 'a', 'a'), null);
  assert.equal(planCardDrop(records, 'missing', 'a'), null);
});

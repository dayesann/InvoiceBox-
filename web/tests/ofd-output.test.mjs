import test from 'node:test';
import assert from 'node:assert/strict';
import { BlobLruCache, sourceDpiForPrint } from '../src/ofd-output.js';

test('OFD is rendered to approximately the requested final print resolution', () => {
  const sourceDpi = sourceDpiForPrint(211.5, 140, 600);
  const scale = Math.min(555 * 25.4 / (72 * 211.5), 373 * 25.4 / (72 * 140));
  assert.ok(sourceDpi / scale >= 600);
  assert.ok(sourceDpi / scale < 602);
  assert.equal(sourceDpiForPrint(211.5, 140, 1200), Math.ceil(1200 * scale));
});

test('page orientation and full-page layouts use the actual print slot', () => {
  const horizontal = sourceDpiForPrint(211.5, 140, 600);
  const vertical = sourceDpiForPrint(140, 211.5, 600);
  assert.equal(horizontal, vertical);
  assert.notEqual(sourceDpiForPrint(211.5, 140, 600, { special: true, preferPortrait: true }), horizontal);
  assert.equal(sourceDpiForPrint(0, 140, 600), 600);
  assert.equal(sourceDpiForPrint(NaN, 140, 1200), 1200);
});

test('bounded LRU cache evicts old blobs and clears removed files', () => {
  const cache = new BlobLruCache(5);
  cache.set('a:1', new Blob(['123']));
  cache.set('b:1', new Blob(['12']));
  assert.ok(cache.get('a:1'));
  cache.set('c:1', new Blob(['1']));
  assert.equal(cache.get('b:1'), undefined);
  assert.equal(cache.bytes, 4);
  cache.deletePrefix('a:');
  assert.equal(cache.bytes, 1);
  cache.set('oversized', new Blob(['123456']));
  assert.equal(cache.get('oversized'), undefined);
  cache.clear();
  assert.equal(cache.bytes, 0);
});

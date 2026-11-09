import test from 'node:test';
import assert from 'node:assert/strict';
import { embeddedPageGeometry, visiblePageBox } from '../src/pdf-page.js';

function page(media, crop, rotation = 0) {
  return { getMediaBox: () => media, getCropBox: () => crop, getRotation: () => ({ angle: rotation }) };
}
const box = (x, y, width, height) => ({ x, y, width, height });

test('a horizontal invoice CropBox on a portrait MediaBox remains horizontal', () => {
  const source = page(box(0, 0, 595, 842), box(0, 377, 595, 465));
  assert.deepEqual(visiblePageBox(source), { left: 0, bottom: 377, right: 595, top: 842 });
  const geometry = embeddedPageGeometry({ width: 595, height: 465 }, source);
  assert.ok(geometry.width > geometry.height);
  assert.equal(geometry.rotation, 0);
});

test('the visible area is intersected with the source page and invalid crops fall back to MediaBox', () => {
  const media = box(10, 20, 500, 700);
  assert.deepEqual(visiblePageBox(page(media, box(-10, 100, 600, 300))), { left: 10, bottom: 100, right: 510, top: 400 });
  assert.deepEqual(visiblePageBox(page(media, box(900, 1000, 1, 1))), { left: 10, bottom: 20, right: 510, top: 720 });
});

test('PDF page rotation changes display dimensions without altering source dimensions', () => {
  for (const angle of [90, -90, 270, 450]) {
    const geometry = embeddedPageGeometry({ width: 595, height: 465 }, page(box(0, 0, 595, 465), box(0, 0, 595, 465), angle));
    assert.equal(geometry.width, 465);
    assert.equal(geometry.height, 595);
    assert.equal(geometry.sourceWidth, 595);
    assert.equal(geometry.sourceHeight, 465);
  }
});

test('a normal landscape page retains its dimensions and the original draw call', () => {
  const embedded = { width: 595, height: 465 };
  const geometry = embeddedPageGeometry(embedded, page(box(0, 0, 595, 465), box(0, 0, 595, 465), 0));
  assert.equal(geometry.width, 595);
  assert.equal(geometry.height, 465);
  let drawn;
  const options = { x: 5 };
  geometry.draw({ drawPage: (...args) => { drawn = args; } }, options);
  assert.deepEqual(drawn, [embedded, options]);
});

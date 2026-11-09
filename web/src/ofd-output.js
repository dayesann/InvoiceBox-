// OFD pages are rendered to images. Choose source resolution from the actual
// space on the final A4 page, rather than always rendering the original at 1200 DPI.
export function sourceDpiForPrint(widthMm, heightMm, targetDpi, { special = false, preferPortrait = false } = {}) {
  const target = Number(targetDpi) === 1200 ? 1200 : 600;
  if (!(widthMm > 0 && heightMm > 0 && Number.isFinite(widthMm) && Number.isFinite(heightMm))) return target;
  const rotate = preferPortrait ? widthMm > heightMm : heightMm > widthMm;
  const width = rotate ? heightMm : widthMm;
  const height = rotate ? widthMm : heightMm;
  const slotWidthPt = special ? 515 : 555;
  const slotHeightPt = special ? 744 : 373;
  const scale = Math.min(slotWidthPt * 25.4 / (72 * width), slotHeightPt * 25.4 / (72 * height));
  return Math.ceil(Math.max(72, Math.min(6000, target * scale)));
}

export class BlobLruCache {
  constructor(maxBytes = 64 * 1024 * 1024) {
    this.maxBytes = maxBytes;
    this.bytes = 0;
    this.items = new Map();
  }
  get(key) {
    const blob = this.items.get(key);
    if (!blob) return undefined;
    this.items.delete(key);
    this.items.set(key, blob);
    return blob;
  }
  set(key, blob) {
    if (!key || !blob || !Number.isFinite(blob.size)) return;
    this.delete(key);
    if (blob.size > this.maxBytes) return;
    while (this.bytes + blob.size > this.maxBytes && this.items.size) this.delete(this.items.keys().next().value);
    this.items.set(key, blob);
    this.bytes += blob.size;
  }
  delete(key) {
    const blob = this.items.get(key);
    if (!blob) return;
    this.bytes -= blob.size;
    this.items.delete(key);
  }
  deletePrefix(prefix) {
    for (const key of this.items.keys()) if (key.startsWith(prefix)) this.delete(key);
  }
  clear() { this.items.clear(); this.bytes = 0; }
}

export const ofdBlobCache = new BlobLruCache();

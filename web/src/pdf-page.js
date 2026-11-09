/** Honor the source PDF's visible CropBox, limited to its physical MediaBox. */
export function visiblePageBox(page) {
  const crop = page.getCropBox();
  const media = page.getMediaBox();
  const left = Math.max(crop.x, media.x);
  const bottom = Math.max(crop.y, media.y);
  const right = Math.min(crop.x + crop.width, media.x + media.width);
  const top = Math.min(crop.y + crop.height, media.y + media.height);
  return right > left && top > bottom
    ? { left, bottom, right, top }
    : { left: media.x, bottom: media.y, right: media.x + media.width, top: media.y + media.height };
}

/** Supply the layout with displayed dimensions while retaining raw source dimensions. */
export function embeddedPageGeometry(embedded, source) {
  const rotation = ((source.getRotation().angle % 360) + 360) % 360;
  const swapsSides = rotation === 90 || rotation === 270;
  return {
    width: swapsSides ? embedded.height : embedded.width,
    height: swapsSides ? embedded.width : embedded.height,
    sourceWidth: embedded.width,
    sourceHeight: embedded.height,
    rotation,
    draw: (destination, options) => destination.drawPage(embedded, options)
  };
}

/**
 * Resolve invoice / itinerary relationships from the current document set.
 * An automatic relationship is a suggestion, never a financial confirmation.
 */
export function documentRole(record) {
  return record.document_role || (record.category === '行程单' ? 'attachment' : 'expense');
}

function originalName(record) {
  return String(record.original_name || record.file?.name || '').normalize('NFKC');
}

// Only the platform's structured batch name is treated as a strong name hint.
// User-editable display names never participate in matching.
function batchKey(record) {
  const name = originalName(record).replace(/\.[^.]+$/, '');
  const bracket = name.match(/^(?:【([^】]+)】|\[([^\]]+)\])/);
  const key = (bracket?.[1] || bracket?.[2] || '').trim().toLowerCase();
  if (/^.+[-_－—]\d+(?:\.\d+)?元[-_－—]\d+个行程$/.test(key)) return key;
  const suffix = name.match(/^(.+?)[\s_－—-]+(?:发票|附单\s*\d*|行程单)$/);
  return suffix ? suffix[1].trim().toLowerCase() : '';
}

// Some downloaders preserve a numeric index on a generic invoice/trip pair.
// This is a hint only: independently downloaded files can have different order.
function genericFileIndex(record) {
  const stem = originalName(record).replace(/\.[^.]+$/, '').trim().toLowerCase();
  const prefix = stem.match(/^(\d+)[\s_-]+(?:invoice|trip)$/);
  if (prefix) return `prefix:${Number(prefix[1])}`;
  const suffix = stem.match(/^(?:invoice|trip)\s*\((\d+)\)$/);
  if (suffix) return `suffix:${Number(suffix[1])}`;
  return /^(?:invoice|trip)$/.test(stem) ? 'base:0' : '';
}

function cents(record) {
  const amount = Number(record.amount);
  return Number.isFinite(amount) && amount > 0 ? Math.round(amount * 100) : null;
}

function transportKind(record) {
  const text = `${record.recognized_text || ''} ${originalName(record)}`;
  const taxi = /网约车|网络预约出租|出租汽车|出租车|打车|滴滴出行|高德打车|曹操出行/i.test(text);
  const transit = /地铁|轨道交通|城市轨道|公交|公共汽车|轻轨/i.test(text);
  const fromText = taxi === transit ? '' : taxi ? 'taxi' : 'transit';
  const fromCategory = record.category === '打车费' ? 'taxi' : record.category === '公共交通' ? 'transit' : '';
  if (fromCategory && fromText && fromCategory !== fromText) return 'conflict';
  return fromCategory || fromText;
}

function edgeFor(attachment, expense) {
  // Automatically consider only transport expenses; other types remain manually selectable.
  if (!['打车费', '公共交通', '交通'].includes(expense.category)) return null;
  const aKind = transportKind(attachment);
  const eKind = transportKind(expense);
  if (aKind === 'conflict' || eKind === 'conflict' || (aKind && eKind && aKind !== eKind)) return null;
  const aCents = cents(attachment);
  const eCents = cents(expense);
  if (aCents !== null && eCents !== null && aCents !== eCents) return null;
  const aKey = batchKey(attachment);
  const eKey = batchKey(expense);
  const sameBatch = Boolean(aKey && eKey && aKey === eKey);
  const sameAmount = aCents !== null && eCents !== null && aCents === eCents;
  if (!sameAmount && !sameBatch) return null;
  const aIndex = genericFileIndex(attachment);
  const eIndex = genericFileIndex(expense);
  const sameFileIndex = Boolean(aIndex && eIndex && aIndex === eIndex);
  const sameExtractedDate = Boolean(attachment.date_start && expense.date_start && attachment.date_start === expense.date_start);
  const routeA = String(attachment.route || '').replace(/\s/g, '');
  const routeE = String(expense.route || '').replace(/\s/g, '');
  const sameRoute = Boolean(routeA && routeE && routeA === routeE);
  const evidence = [];
  if (sameAmount) evidence.push('金额一致');
  if (aKind && eKind && aKind === eKind) evidence.push('交通类型一致');
  if (sameBatch) evidence.push('原文件批次名称一致');
  if (sameFileIndex) evidence.push(aIndex === 'base:0' ? '原文件名对应' : '原文件序号一致');
  if (sameExtractedDate) evidence.push('提取日期一致（需核对含义）');
  if (sameRoute) evidence.push('路线一致');
  return { attachment, expense, score: (sameBatch ? 100 : 0) + (sameFileIndex ? 40 : 0) + (sameAmount ? 20 : 0) + (aKind && eKind && aKind === eKind ? 10 : 0) + (sameExtractedDate ? 1 : 0) + (sameRoute ? 2 : 0), evidence };
}

function manualWarning(attachment, expense) {
  const warnings = [];
  const a = cents(attachment);
  const b = cents(expense);
  if (a !== null && b !== null && a !== b) warnings.push('金额不一致');
  const ak = transportKind(attachment);
  const bk = transportKind(expense);
  if (ak === 'conflict' || bk === 'conflict' || (ak && bk && ak !== bk)) warnings.push('交通类型可能不一致');
  if (ak && ak !== 'conflict' && !['打车费', '公共交通', '交通'].includes(expense.category)) warnings.push('费用类别可能不匹配');
  return warnings.join('、');
}

function uniqueBest(edges) {
  if (!edges.length) return null;
  let best = edges[0];
  let count = 1;
  for (const edge of edges.slice(1)) {
    if (edge.score > best.score) { best = edge; count = 1; }
    else if (edge.score === best.score) count++;
  }
  return count === 1 ? best : null;
}

export function resolveAssociations(records) {
  const output = records.map(record => ({ ...record }));
  const expenses = output.filter(record => documentRole(record) === 'expense');
  const attachments = output.filter(record => documentRole(record) === 'attachment');
  const byId = new Map(expenses.map(record => [record.id, record]));
  const occupied = new Set();
  const available = [];
  const previousSuggestions = new Map();

  for (const attachment of attachments) {
    const parent = byId.get(attachment.parent_id);
    if (attachment.association_origin === 'manual' && parent) {
      occupied.add(parent.id);
      Object.assign(attachment, { association_state: 'confirmed', association_reason: '已手动确认', association_warning: manualWarning(attachment, parent), association_candidates: [] });
    } else if (attachment.association_origin === 'rejected') {
      Object.assign(attachment, { parent_id: '', association_state: 'unmatched', association_reason: '已手动选择不关联', association_warning: '', association_candidates: [] });
    } else {
      if (attachment.association_origin === 'auto' && attachment.association_state === 'suggested' && parent) previousSuggestions.set(attachment.id, parent.id);
      Object.assign(attachment, { parent_id: '', association_origin: 'auto', association_state: 'unmatched', association_reason: '尚未找到唯一候选', association_warning: '', association_candidates: [] });
      available.push(attachment);
    }
  }

  const edges = [];
  for (const attachment of available) {
    for (const expense of expenses) {
      if (occupied.has(expense.id) && previousSuggestions.get(attachment.id) !== expense.id) continue;
      const edge = edgeFor(attachment, expense);
      if (edge) edges.push(edge);
    }
  }
  const byAttachment = new Map(available.map(record => [record.id, []]));
  const byExpense = new Map(expenses.map(record => [record.id, []]));
  for (const edge of edges) {
    byAttachment.get(edge.attachment.id).push(edge);
    byExpense.get(edge.expense.id).push(edge);
  }
  for (const attachment of available) {
    const options = byAttachment.get(attachment.id);
    attachment.association_candidates = options.map(edge => edge.expense.id).sort();
    const best = uniqueBest(options);
    if (best && uniqueBest(byExpense.get(best.expense.id)) === best) {
      Object.assign(attachment, { parent_id: best.expense.id, association_state: 'suggested', association_reason: `${best.evidence.join('、')}；可拖拽调整` });
    } else if (options.length) {
      attachment.association_reason = '存在多个可能关联，请手动选择';
    }
  }
  return output;
}

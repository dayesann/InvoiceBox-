import { documentRole } from './matching.js';

/** Plan a card reorder; dropping an attachment on an expense also links them. */
export function planCardDrop(records, sourceId, targetId) {
  const sourceIndex = records.findIndex(item => item.id === sourceId);
  const targetIndex = records.findIndex(item => item.id === targetId);
  if (sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) return null;
  const source = records[sourceIndex];
  const target = records[targetIndex];
  const moved = [...records];
  moved.splice(sourceIndex, 1);
  const targetPosition = moved.findIndex(item => item.id === targetId);
  moved.splice(targetPosition + (sourceIndex < targetIndex ? 1 : 0), 0, source);

  let attachment;
  let expense;
  if (documentRole(source) === 'attachment' && documentRole(target) === 'expense') {
    attachment = source;
    expense = target;
  } else if (documentRole(source) === 'expense' && documentRole(target) === 'attachment') {
    attachment = target;
    expense = source;
  }
  if (!attachment) return { records: moved, attachmentId: '', expenseId: '' };

  // Keep a manually paired document next to its expense in the left list.
  moved.splice(moved.findIndex(item => item.id === attachment.id), 1);
  let insertion = moved.findIndex(item => item.id === expense.id) + 1;
  while (insertion < moved.length && documentRole(moved[insertion]) === 'attachment' && moved[insertion].parent_id === expense.id) insertion++;
  moved.splice(insertion, 0, attachment);
  return { records: moved, attachmentId: attachment.id, expenseId: expense.id };
}

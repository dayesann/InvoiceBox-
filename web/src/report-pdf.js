/** Add the report to the output document before its invoice pages are drawn. */
export async function appendReport(pdf, element, { render, duplex, printScaling }) {
  await document.fonts.ready;
  const preferences = pdf.catalog.getOrCreateViewerPreferences();
  preferences.setDuplex(duplex);
  preferences.setPrintScaling(printScaling);
  const sheets = [...element.querySelectorAll('.report-sheet')];
  for (const sheet of sheets.length ? sheets : [element]) {
    const canvas = await render(sheet, {
      scale: 300 / 96,
      backgroundColor: '#ffffff',
      onclone(doc) {
        const report = doc.querySelector('.report-paper');
        report.style.display = 'block';
        report.style.margin = '0';
        report.style.boxShadow = 'none';
      }
    });
    const image = await pdf.embedPng(canvas.toDataURL('image/png'));
    pdf.addPage([595, 842]).drawImage(image, { x: 0, y: 0, width: 595, height: 842 });
    canvas.width = 0;
    canvas.height = 0;
  }
}

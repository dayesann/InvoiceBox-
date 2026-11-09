import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
import fitz


def expense(id, amount=10, **kwargs):
    return dict(id=id, amount=amount, category='打车费', original_name=id+'.pdf', batch_id='', **kwargs)


def trip(id, amount=10, **kwargs):
    return dict(id=id, amount=amount, category='行程单', original_name=id+'.pdf', batch_id='', **kwargs)


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = {k: getattr(main, k) for k in ('DATA_DIR', 'UPLOAD_DIR', 'DB_FILE', 'SETTINGS_FILE', 'BACKUP_DIR', 'MERGED_PDF')}
        main.DATA_DIR = self.temp.name
        main.UPLOAD_DIR = os.path.join(self.temp.name, 'invoices')
        os.makedirs(main.UPLOAD_DIR)
        main.DB_FILE = os.path.join(self.temp.name, 'database.json')
        main.SETTINGS_FILE = os.path.join(self.temp.name, 'settings.json')
        main.BACKUP_DIR = os.path.join(self.temp.name, 'backups')
        main.MERGED_PDF = os.path.join(main.UPLOAD_DIR, 'merged_preview.pdf')
        self.client = main.app.test_client()

    def tearDown(self):
        for key, value in self.old.items(): setattr(main, key, value)
        self.temp.cleanup()

    def test_unique_and_ambiguous(self):
        rows = main.resolve_associations([expense('invoice'), trip('trip')])
        self.assertEqual(rows[1]['parent_id'], 'invoice')
        ambiguous = [expense('a'), expense('b'), trip('c')]
        for order in (ambiguous, list(reversed(ambiguous))):
            self.assertEqual(next(r for r in main.resolve_associations(order) if r['id']=='c')['parent_id'], '')

    def test_conflicts_and_batches(self):
        for changed in ({'amount':11}, {'document_type':'地铁行程单'}, {'batch_id':'other'}):
            row = trip('b'); row.update(changed)
            self.assertEqual(main.resolve_associations([expense('a'), row])[1]['parent_id'], '')

    def test_filename_tie_break_and_rename(self):
        rows = [expense('invoice'), expense('1-invoice'), trip('trip'), trip('1-trip')]
        for row in rows: row['display_name'] = 'renamed.pdf'
        result = main.resolve_associations(rows)
        self.assertEqual(result[2]['parent_id'], 'invoice')
        self.assertEqual(result[3]['parent_id'], '1-invoice')

    def test_manual_association_persistence(self):
        main.save_db({'invoices':[expense('a'), expense('b'), trip('c'), trip('d')]})
        for id in ('c','d'):
            self.assertEqual(self.client.post('/api/associate', json={'id':id,'parent_id':'b'}).status_code,200)
        self.client.post('/api/reorder',json={'ids':['d','c','b','a']})
        rows=self.client.get('/api/init').json['invoices']
        self.assertEqual([r['id'] for r in rows],['d','c','b','a'])
        self.assertTrue(all(r['parent_id']=='b' for r in rows[:2]))
        self.client.post('/api/associate',json={'id':'c','parent_id':''})
        self.assertEqual(next(r for r in main.load_db()['invoices'] if r['id']=='c')['parent_id'],'')
        self.assertEqual(self.client.post('/api/associate',json={'id':'d','parent_id':'missing'}).status_code,400)

    def test_multipage_cropbox_export(self):
        document=fitz.open()
        for n in range(3):
            page=document.new_page(width=595,height=842)
            page.insert_text((40,420), 'PAGE-'+str(n))
            page.set_cropbox(fitz.Rect(0,377,595,842))
        path=os.path.join(main.UPLOAD_DIR,'multi.pdf'); document.save(path); document.close()
        row=expense('a',name='multi.pdf'); row['category']='办公'
        main.save_db({'invoices':[row]})
        pages, mapping=main._build_layout(['a'])
        self.assertEqual(len(pages),2); self.assertEqual(mapping['a'],0)
        response=self.client.post('/api/download_pdf',json={'ids':['a']})
        self.assertEqual(response.status_code,200)
        with fitz.open(main.MERGED_PDF) as output:
            text=''.join(p.get_text() for p in output)
            for n in range(3): self.assertIn('PAGE-'+str(n),text)
            self.assertEqual(len(output),2)

    def test_upload_rename_and_restart(self):
        document=fitz.open(); page=document.new_page(); page.insert_text((40,40),'Sample 10.00')
        data=document.tobytes(); document.close()
        response=self.client.post('/api/upload',data={'file':(io.BytesIO(data),'invoice.pdf')})
        self.assertEqual(response.status_code,200)
        id=response.json['id']
        self.client.post('/api/update',json={'id':id,'display_name':'other.pdf'})
        self.assertEqual(main.load_db()['invoices'][0]['original_name'],'invoice.pdf')
        self.assertEqual(self.client.get('/api/init').json['version'],'6.6.0')

    def test_pair_invoice_first_and_special_duplicates(self):
        for name in ('invoice','trip'):
            doc=fitz.open(); page=doc.new_page(); page.insert_text((40,40),name)
            doc.save(os.path.join(main.UPLOAD_DIR,name+'.pdf'));doc.close()
        main.save_db({'invoices':[expense('invoice',name='invoice.pdf'),trip('trip',name='trip.pdf')]})
        pages,_=main._build_layout(['trip','invoice'])
        self.assertEqual(len(pages),1)
        self.assertTrue(pages[0][0][0][0].endswith('invoice.pdf'))
        main.save_settings({**main.DEFAULT_SETTINGS,'special_categories':['打车费']})
        pages,mapping=main._build_layout(['invoice'],True)
        self.assertEqual(len(pages),2);self.assertEqual(mapping['invoice'],0)


if __name__=='__main__': unittest.main()

import io
import json
import os
import shutil
import sys
import tempfile
import zipfile

import fitz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main


def run():
    root = tempfile.mkdtemp(prefix="invoicebox-product-test-")
    try:
        main.DATA_DIR = root
        main.UPLOAD_DIR = os.path.join(root, "invoices")
        main.DB_FILE = os.path.join(root, "database.json")
        main.SETTINGS_FILE = os.path.join(root, "settings.json")
        main.BACKUP_DIR = os.path.join(root, "backups")
        main.MERGED_PDF = os.path.join(main.UPLOAD_DIR, "merged_preview.pdf")
        os.makedirs(main.UPLOAD_DIR, exist_ok=True)

        pdf_name = "sample.pdf"
        pdf_path = os.path.join(main.UPLOAD_DIR, pdf_name)
        doc = fitz.open()
        doc.new_page(width=300, height=200)
        doc.save(pdf_path)
        doc.close()

        main.save_db(
            {
                "invoices": [
                    {
                        "id": "invoice-1",
                        "name": pdf_name,
                        "display_name": "sample.pdf",
                        "amount": 12.5,
                        "category": "办公",
                        "date": "2026-06-19",
                        "date_start": "",
                        "date_end": "",
                        "route": "",
                        "md5": "abc",
                    }
                ]
            }
        )
        main.save_settings(
            {
                "company_name": "Test Co",
                "report_title": "Test Report",
                "currency_symbol": "$",
                "categories": ["办公", "其他"],
                "special_categories": ["办公"],
                "excluded_report_categories": ["其他"],
                "duplicate_special": True,
            }
        )

        client = main.app.test_client()
        init = client.get("/api/init")
        assert init.status_code == 200
        assert init.get_json()["version"] == "6.2"
        assert init.get_json()["settings"]["special_categories"] == ["办公"]

        bad_update = client.post(
            "/api/update",
            json={"id": "invoice-1", "category": "不存在"},
        )
        assert bad_update.status_code == 400

        good_update = client.post(
            "/api/update",
            json={"id": "invoice-1", "amount": "19.90", "name": "hacked.pdf"},
        )
        assert good_update.status_code == 200
        assert good_update.get_json()["amount"] == 19.9
        assert good_update.get_json()["name"] == pdf_name

        pages, page_map = main._build_layout(
            ["invoice-1"],
            duplicate_special=True,
        )
        assert len(pages) == 2
        assert page_map["invoice-1"] == 0

        backup = client.get("/api/backup")
        assert backup.status_code == 200
        backup_bytes = backup.get_data()
        with zipfile.ZipFile(io.BytesIO(backup_bytes)) as archive:
            assert {
                "manifest.json",
                "database.json",
                "settings.json",
                "invoices/sample.pdf",
            } <= set(archive.namelist())

        main.save_db({"invoices": []})
        main.save_settings(main.DEFAULT_SETTINGS)
        restore = client.post(
            "/api/restore",
            data={"file": (io.BytesIO(backup_bytes), "backup.zip")},
            content_type="multipart/form-data",
        )
        assert restore.status_code == 200, restore.get_data(as_text=True)
        assert restore.get_json()["invoice_count"] == 1
        assert main.load_db()["invoices"][0]["amount"] == 19.9
        assert main.load_settings()["company_name"] == "Test Co"
        assert os.path.exists(os.path.join(main.UPLOAD_DIR, pdf_name))

        print(
            json.dumps(
                {
                    "init": "ok",
                    "settings": "ok",
                    "update_validation": "ok",
                    "layout_rules": "ok",
                    "backup_restore": "ok",
                },
                ensure_ascii=False,
            )
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    run()

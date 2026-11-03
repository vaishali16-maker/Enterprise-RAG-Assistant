from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from fastapi import HTTPException

from backend import main
from backend.auth import CurrentUser, get_current_user
from backend.document_permissions import ensure_document_manager, normalize_shared_roles, validate_pdf_upload
from backend.security import SlidingWindowRateLimiter, rate_limiter


class DocumentPermissionUnitTests(unittest.TestCase):
    def test_admin_and_hr_can_share_all_roles(self):
        roles = ["employee", "manager", "hr", "admin"]
        self.assertEqual(normalize_shared_roles("admin", roles), sorted(roles))
        self.assertEqual(normalize_shared_roles("hr", roles), sorted(roles))

    def test_manager_share_scope_and_automatic_hr_admin(self):
        self.assertEqual(
            normalize_shared_roles("manager", ["employee"]),
            ["admin", "employee", "hr", "manager"],
        )
        with self.assertRaises(HTTPException) as raised:
            normalize_shared_roles("manager", ["hr"])
        self.assertEqual(raised.exception.status_code, 403)
        with self.assertRaises(HTTPException) as raised:
            normalize_shared_roles("manager", ["not-a-role"])
        self.assertEqual(raised.exception.status_code, 403)

    def test_employee_cannot_share_or_manage(self):
        with self.assertRaises(HTTPException) as raised:
            normalize_shared_roles("employee", ["employee"])
        self.assertEqual(raised.exception.status_code, 403)
        with self.assertRaises(HTTPException) as raised:
            ensure_document_manager("employee-1", "employee", {"uploaded_by": "employee-1"})
        self.assertEqual(raised.exception.status_code, 403)

    def test_manager_can_manage_only_own_uploads(self):
        ensure_document_manager("manager-1", "manager", {"uploaded_by": "manager-1"})
        with self.assertRaises(HTTPException) as raised:
            ensure_document_manager("manager-1", "manager", {"uploaded_by": "manager-2"})
        self.assertEqual(raised.exception.status_code, 403)
        ensure_document_manager("hr-1", "hr", {"uploaded_by": "manager-2"})
        ensure_document_manager("admin-1", "admin", {"uploaded_by": None})

    def test_pdf_type_and_size_validation(self):
        validate_pdf_upload("ok.pdf", b"%PDF-1.7 test")
        for filename, data in (("bad.txt", b"%PDF-1.7"), ("bad.pdf", b"not a pdf")):
            with self.subTest(filename=filename), self.assertRaises(HTTPException) as raised:
                validate_pdf_upload(filename, data)
            self.assertEqual(raised.exception.status_code, 400)
        with self.assertRaises(HTTPException) as raised:
            validate_pdf_upload("large.pdf", b"%PDF-" + b"x" * (20 * 1024 * 1024))
        self.assertEqual(raised.exception.status_code, 413)

    def test_rate_limiter_isolated_by_user_and_bucket_and_expires(self):
        limiter = SlidingWindowRateLimiter()
        self.assertEqual(limiter.allow("u1", "upload", 1, 10, now=100), (True, 0))
        self.assertEqual(limiter.allow("u1", "upload", 1, 10, now=101)[0], False)
        self.assertEqual(limiter.allow("u2", "upload", 1, 10, now=101), (True, 0))
        self.assertEqual(limiter.allow("u1", "question", 1, 10, now=101), (True, 0))
        self.assertEqual(limiter.allow("u1", "upload", 1, 10, now=111), (True, 0))


class DocumentEndpointPermissionTests(unittest.TestCase):
    def setUp(self):
        rate_limiter.reset()
        self.user = CurrentUser("user-1", "tenant-1", "employee", "user@example.com")
        main.app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        main.app.dependency_overrides.clear()
        rate_limiter.reset()

    def set_role(self, role: str, user_id: str = "user-1"):
        self.user = CurrentUser(user_id, "tenant-1", role, f"{role}@example.com")

    def test_employee_gets_403_for_upload_delete_and_access_change(self):
        upload = self.client.post("/upload", files={"file": ("a.pdf", b"%PDF-1.7", "application/pdf")})
        deleted = self.client.delete("/documents/doc-1")
        changed = self.client.patch("/documents/doc-1/access", json={"allowed_roles": ["employee"]})
        self.assertEqual([upload.status_code, deleted.status_code, changed.status_code], [403, 403, 403])

    def test_upload_succeeds_for_permitted_roles_and_audits(self):
        for role, expected_roles in (
            ("admin", ["employee"]),
            ("hr", ["employee"]),
            ("manager", ["admin", "employee", "hr", "manager"]),
        ):
            with self.subTest(role=role):
                self.set_role(role)
                rate_limiter.reset()
                with patch.object(main, "find_documents_by_name", return_value=[]), \
                     patch.object(main, "ingest_pdf", return_value={"document_id": "doc-1", "name": "a.pdf", "chunks": 1}) as ingest, \
                     patch.object(main, "_write_document_audit") as audit, \
                     patch.object(main, "extract_text_from_pdf", return_value="text"), \
                     patch.object(main, "clean_extracted_text", return_value="text"), \
                     patch.object(main, "generate_faq", return_value=[]):
                    response = self.client.post(
                        "/upload", data={"allowed_roles": "employee"},
                        files={"file": ("a.pdf", b"%PDF-1.7 test", "application/pdf")},
                    )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(ingest.call_args.kwargs["allowed_roles"], expected_roles)
                audit.assert_called_once()
                self.assertEqual(audit.call_args.args[1], "upload")

    def test_manager_cannot_select_hr_or_admin_for_upload(self):
        self.set_role("manager")
        response = self.client.post(
            "/upload", data={"allowed_roles": "hr"},
            files={"file": ("a.pdf", b"%PDF-1.7", "application/pdf")},
        )
        self.assertEqual(response.status_code, 403)

    def test_manager_delete_ownership_and_admin_hr_access(self):
        with patch.object(main, "_get_tenant_document", return_value={"id": "doc-1", "name": "a.pdf", "uploaded_by": "other", "allowed_roles": []}), \
             patch.object(main, "delete_document") as delete, patch.object(main, "_write_document_audit") as audit:
            self.set_role("manager")
            response = self.client.delete("/documents/doc-1")
            self.assertEqual(response.status_code, 403)
            delete.assert_not_called()
            audit.assert_not_called()

        for role in ("manager", "hr", "admin"):
            owner = "user-1" if role == "manager" else "other-user"
            with self.subTest(role=role), patch.object(main, "_get_tenant_document", return_value={"id": "doc-1", "name": "a.pdf", "uploaded_by": owner, "allowed_roles": []}), \
                 patch.object(main, "delete_document", return_value=True), patch.object(main, "_write_document_audit") as audit:
                self.set_role(role)
                response = self.client.delete("/documents/doc-1")
                if role == "manager":
                    self.assertEqual(response.status_code, 200, response.text)
                else:
                    self.assertEqual(response.status_code, 200, response.text)
                audit.assert_called_once()
                self.assertEqual(audit.call_args.args[1], "delete")

    def test_access_update_is_role_scoped_and_audited(self):
        self.set_role("manager")
        document = {"id": "doc-1", "name": "a.pdf", "tenant_id": "tenant-1", "uploaded_by": "user-1", "allowed_roles": ["employee"]}

        class Query:
            def select(self, *_): return self
            def update(self, *_): return self
            def eq(self, *_): return self
            def limit(self, *_): return self
            def execute(self): return SimpleNamespace(data=[document])

        with patch.object(main, "_get_tenant_document", return_value=document), \
             patch.object(main.supabase, "table", return_value=Query()), \
             patch.object(main, "_write_document_audit") as audit:
            response = self.client.patch("/documents/doc-1/access", json={"allowed_roles": ["employee", "manager"]})
        self.assertEqual(response.status_code, 200, response.text)
        audit.assert_called_once()
        self.assertEqual(audit.call_args.args[1], "access_change")

        with patch.object(main, "_get_tenant_document", return_value={**document, "uploaded_by": "other"}):
            denied = self.client.patch("/documents/doc-1/access", json={"allowed_roles": ["employee"]})
        self.assertEqual(denied.status_code, 403)

    def test_bad_pdf_and_upload_size_are_rejected(self):
        self.set_role("hr")
        response = self.client.post("/upload", files={"file": ("bad.txt", b"%PDF-1.7", "application/pdf")})
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/upload", files={"file": ("large.pdf", b"%PDF-" + b"x" * (20 * 1024 * 1024), "application/pdf")})
        self.assertEqual(response.status_code, 413)

    def test_questions_are_rate_limited_per_user(self):
        self.set_role("employee")
        with patch.object(main, "QUESTION_RATE_LIMIT", 1), \
             patch.object(main, "search_documents", return_value=[]), \
             patch.object(main, "generate_answer", return_value="An answer"):
            first = self.client.post("/ask", json={"question": "Question one?"})
            second = self.client.post("/ask", json={"question": "Question two?"})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 429, second.text)

    def test_uploads_are_rate_limited_per_user(self):
        self.set_role("admin")
        rate_limiter.reset()
        with patch.object(main, "UPLOAD_RATE_LIMIT", 1), \
             patch.object(main, "find_documents_by_name", return_value=[]), \
             patch.object(main, "ingest_pdf", return_value={"document_id": "doc-1", "name": "a.pdf", "chunks": 1}), \
             patch.object(main, "_write_document_audit"), \
             patch.object(main, "extract_text_from_pdf", return_value="text"), \
             patch.object(main, "clean_extracted_text", return_value="text"), \
             patch.object(main, "generate_faq", return_value=[]):
            body = {"file": ("a.pdf", b"%PDF-1.7 test", "application/pdf")}
            first = self.client.post("/upload", files=body)
            second = self.client.post("/upload", files=body)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 429, second.text)


if __name__ == "__main__":
    unittest.main()

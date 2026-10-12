import pytest
import json
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app import app
from config import settings

client = TestClient(app)

def test_slr_config_endpoint():
    res = client.get("/api/slr/config")
    assert res.status_code == 200
    data = res.json()
    assert "base_url" in data
    assert "has_api_key" in data
    assert "default_project_id" in data

def test_slr_update_config():
    payload = {
        "base_url": "http://127.0.0.1:5000",
        "api_key": "test-key-12345",
        "default_project_id": "proj-test-1"
    }
    res = client.post("/api/slr/config", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["config"]["base_url"] == "http://127.0.0.1:5000"
    assert data["config"]["default_project_id"] == "proj-test-1"

@patch("requests.get")
def test_slr_projects_endpoint_success(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "isSuccess": True,
        "data": [
            {"id": "proj-1", "name": "Software Architecture SLR", "status": "ACTIVE"},
            {"id": "proj-2", "name": "Machine Learning in Healthcare", "status": "ACTIVE"}
        ]
    }
    mock_get.return_value = mock_resp

    res = client.get("/api/slr/projects")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert len(data["projects"]) == 2
    assert data["projects"][0]["id"] == "proj-1"

@patch("requests.get")
def test_slr_projects_endpoint_unauthorized(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = "Unauthorized"
    mock_get.return_value = mock_resp

    res = client.get("/api/slr/projects")
    assert res.status_code == 401
    data = res.json()
    assert data["success"] is False
    assert "401" in data["error"]

def test_slr_push_missing_project_id():
    res = client.post("/api/slr/push-papers", json={"project_id": "", "dois": ["10.1234/test"]})
    assert res.status_code == 400
    data = res.json()
    assert "Mã Dự Án" in data["error"]

def test_slr_push_empty_dois():
    res = client.post("/api/slr/push-papers", json={"project_id": "proj-1", "dois": []})
    assert res.status_code == 400
    data = res.json()
    assert "Chưa chọn bài báo" in data["error"]

@patch("requests.post")
def test_slr_push_streaming_success(mock_post):
    # Setup mock paper in store
    from paper_store import get_store
    from pathlib import Path
    store = get_store()
    test_doi = "10.1016/test.paper.2026"
    test_file = settings.DOWNLOAD_FOLDER / "test_paper_push.pdf"
    test_file.write_bytes(b"%PDF-1.4\nTest PDF content for push\n%%EOF")

    store.update(test_doi, "DOWNLOADED", filename=test_file.name, file_path=str(test_file.resolve()))

    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {
        "isSuccess": True,
        "data": {
            "importedRecords": 1,
            "importedPaperIds": ["paper-pub-abc-123"]
        }
    }
    mock_post.return_value = mock_resp

    res = client.post("/api/slr/push-papers", json={
        "project_id": "proj-slr-demo",
        "dois": [test_doi]
    })

    assert res.status_code == 200
    lines = [line for line in res.text.split("\n") if line.strip()]
    assert len(lines) >= 3 # start, item_progress, done

    # Clean up test file
    try:
        test_file.unlink()
    except Exception:
        pass


@patch("requests.post")
def test_slr_push_direct_to_cloud_success(mock_post):
    from paper_store import get_store
    store = get_store()
    test_doi = "10.1016/direct.cloud.2026"
    test_file = settings.DOWNLOAD_FOLDER / "test_direct_cloud.pdf"
    test_file.write_bytes(b"%PDF-1.4\nTest Direct to Cloud PDF content\n%%EOF")

    metadata_dict = {
        "title": "Direct to Cloud Architecture in SLR",
        "authors": "Alice Smith, Bob Jones",
        "year": "2026",
        "journal": "IEEE Transactions on Cloud Computing"
    }

    store.update(test_doi, "DOWNLOADED", filename=test_file.name, file_path=str(test_file.resolve()), metadata=metadata_dict)

    # 1. Signature mock response
    mock_sig_resp = MagicMock()
    mock_sig_resp.status_code = 200
    mock_sig_resp.json.return_value = {
        "isSuccess": True,
        "data": {
            "uploadUrl": "https://api.cloudinary.com/v1_1/demo/raw/upload",
            "apiKey": "mock_api_key",
            "timestamp": 1700000000,
            "signature": "mock_hmac_signature",
            "folder": "slr-system/projects/proj-slr-demo/papers",
            "publicId": "proj-slr-demo_test_direct_cloud_1700000000.pdf",
            "baseName": "proj-slr-demo_test_direct_cloud_1700000000"
        }
    }

    # 2. Cloudinary upload mock response
    mock_cld_resp = MagicMock()
    mock_cld_resp.status_code = 200
    mock_cld_resp.json.return_value = {
        "public_id": "slr-system/projects/proj-slr-demo/papers/proj-slr-demo_test_direct_cloud_1700000000.pdf",
        "secure_url": "https://res.cloudinary.com/demo/raw/upload/v1/slr-system/projects/proj-slr-demo/papers/proj-slr-demo_test_direct_cloud_1700000000.pdf",
        "bytes": len(test_file.read_bytes())
    }

    # 3. Finalize upload mock response
    mock_fin_resp = MagicMock()
    mock_fin_resp.status_code = 201
    mock_fin_resp.json.return_value = {
        "isSuccess": True,
        "data": {
            "importedPaperIds": ["paper-direct-cloud-999"],
            "summary": {"totalFiles": 1, "imported": 1, "skipped": 0, "failed": 0}
        }
    }

    mock_post.side_effect = [mock_sig_resp, mock_cld_resp, mock_fin_resp]

    res = client.post("/api/slr/push-papers", json={
        "project_id": "proj-slr-demo",
        "dois": [test_doi]
    })

    assert res.status_code == 200
    lines = [json.loads(line) for line in res.text.split("\n") if line.strip()]
    
    # Verify streaming events
    assert any(ev.get("type") == "start" for ev in lines)
    progress_ev = next(ev for ev in lines if ev.get("type") == "item_progress")
    assert progress_ev["success"] is True
    assert "paper-direct-cloud-999" in progress_ev["message"]
    done_ev = next(ev for ev in lines if ev.get("type") == "done")
    assert done_ev["success_count"] == 1
    assert done_ev["fail_count"] == 0

    # Ensure 3 requests were made (Signature -> Cloudinary CDN -> Backend Finalize)
    assert mock_post.call_count == 3
    first_call_url = mock_post.call_args_list[0][0][0]
    second_call_url = mock_post.call_args_list[1][0][0]
    third_call_url = mock_post.call_args_list[2][0][0]
    assert "/upload-signature" in first_call_url
    assert "api.cloudinary.com" in second_call_url
    assert "/finalize-upload" in third_call_url

    try:
        test_file.unlink()
    except Exception:
        pass


from __future__ import annotations

import gzip
import json

from timed_dataserver.qc_http import qc_etag, qc_json_response, serialize_qc_json


def test_qc_json_response_gzips_a_stable_json_representation() -> None:
    response = qc_json_response(
        lambda: {"z": "räksmörgås", "a": [2, 1]},
        export_id="fixture-export",
        endpoint="fields",
        blind=False,
        accept_encoding="br, gzip; q=0.8",
    )

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["vary"] == "Accept-Encoding"
    assert response.headers["cache-control"] == "private, no-cache"
    assert response.headers["etag"].startswith('W/"qc-')
    assert gzip.decompress(response.body) == (
        '{"a":[2,1],"z":"räksmörgås"}'.encode()
    )


def test_qc_etags_separate_endpoint_mode_export_and_state() -> None:
    base = qc_etag("export-a", "fields", blind=False)

    assert base != qc_etag("export-b", "fields", blind=False)
    assert base != qc_etag("export-a", "field", blind=False)
    assert base != qc_etag("export-a", "fields", blind=True)
    assert base != qc_etag("export-a", "fields", blind=False, state="changed")
    assert qc_etag(
        "export-a", "field/plate-a/A01/1", blind=False
    ) != qc_etag("export-a", "field/plate-a/A01/2", blind=False)
    assert base == qc_etag("export-a", "fields", blind=False)


def test_matching_etag_skips_payload_construction() -> None:
    etag = qc_etag("fixture-export", "triage", blind=True)
    calls = 0

    def payload() -> object:
        nonlocal calls
        calls += 1
        return {"unexpected": True}

    response = qc_json_response(
        payload,
        export_id="fixture-export",
        endpoint="triage",
        blind=True,
        accept_encoding="gzip",
        if_none_match=f'"unrelated", {etag.removeprefix("W/")}',
    )

    assert response.status_code == 304
    assert response.body == b""
    assert response.headers["etag"] == etag
    assert "content-encoding" not in response.headers
    assert calls == 0


def test_qc_json_response_uses_identity_when_gzip_is_unavailable() -> None:
    response = qc_json_response(
        lambda: {"ok": True},
        export_id="fixture-export",
        endpoint="dictionary",
        blind=False,
        accept_encoding="gzip;q=0, *;q=1",
    )

    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    assert response.body == b'{"ok":true}'


def test_qc_json_response_gzips_error_status() -> None:
    response = qc_json_response(
        lambda: {"error": "invalid request"},
        export_id="fixture-export",
        endpoint="fields/error",
        blind=False,
        accept_encoding="gzip",
        status_code=422,
    )

    assert response.status_code == 422
    assert response.headers["content-encoding"] == "gzip"
    assert json.loads(gzip.decompress(response.body)) == {
        "error": "invalid request"
    }


def test_qc_json_response_preserves_auth_header_and_rebuilds_representation_headers() -> None:
    response = qc_json_response(
        lambda: {"error": "authentication required"},
        export_id="fixture-export",
        endpoint="status/error",
        blind=False,
        status_code=401,
        extra_headers={
            "WWW-Authenticate": 'Bearer realm="QC"',
            "Content-Length": "9999",
            "Content-Encoding": "br",
        },
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer realm="QC"'
    assert response.headers["content-length"] == str(len(response.body))
    assert "content-encoding" not in response.headers
    assert response.body == b'{"error":"authentication required"}'


def test_matching_error_etag_does_not_replace_the_error_with_not_modified() -> None:
    etag = qc_etag("fixture-export", "field/error", blind=False, state="invalid")

    response = qc_json_response(
        lambda: {"error": "invalid request"},
        export_id="fixture-export",
        endpoint="field/error",
        blind=False,
        state="invalid",
        status_code=422,
        if_none_match=etag,
    )

    assert response.status_code == 422
    assert json.loads(response.body) == {"error": "invalid request"}


def test_qc_json_serialization_normalizes_non_finite_numbers_to_null() -> None:
    encoded = serialize_qc_json(
        {
            "finite": 1.25,
            "values": [float("nan"), float("inf"), -float("inf")],
        }
    )

    assert b"NaN" not in encoded
    assert b"Infinity" not in encoded
    assert json.loads(encoded) == {
        "finite": 1.25,
        "values": [None, None, None],
    }

import json
from pathlib import Path

import pytest

from scripts import inspect_timed_metadata


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_report_summarizes_json_csv_and_redacts_credentials(tmp_path: Path) -> None:
    acquisition = tmp_path / "acquisition"
    write_json(
        acquisition / "P1.json",
        {
            "plate": "P1",
            "api_token": "do-not-report",
            "channels": [
                {"wavelength": 405, "exposure_ms": 20},
                {"wavelength": 625, "exposure_ms": 350},
            ],
        },
    )
    write_json(
        acquisition / "P2.json",
        {
            "plate": "P2",
            "api_token": "also-secret",
            "channels": [
                {"wavelength": 405, "exposure_ms": 20},
                {"wavelength": 625, "exposure_ms": 250},
            ],
        },
    )
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    (csv_dir / "layout.csv").write_text(
        "Plate,Well,Compound,Dose\nP1,A01,DMSO,0\nP1,A02,Drug A,1\n",
        encoding="utf-8",
    )
    (tmp_path / "build_metadata_parquet.py").write_text(
        "import csv\n\ndef build():\n    return None\n",
        encoding="utf-8",
    )

    report = inspect_timed_metadata.inspect_metadata(tmp_path)

    assert report["file_counts"] == {
        "json": 2,
        "csv": 1,
        "parquet": 0,
        "python": 1,
        "other": 0,
    }
    fields = {field["path"]: field for field in report["json"]["fields"]}
    assert fields["$.channels[].wavelength"]["distinct_value_count"] == 2
    assert fields["$.api_token"]["top_values"] == [{"value": "<redacted>", "count": 2}]
    varying = {field["path"]: field for field in report["json"]["varying_fields"]}
    assert set(varying["$.plate"]["values_by_file"]) == {
        "acquisition/P1.json",
        "acquisition/P2.json",
    }
    representative = report["json"]["structural_groups"][0]["representative"]
    assert representative["api_token"] == "<redacted>"

    csv_report = report["csv"][0]
    assert csv_report["candidate_roles"]["plate"] == ["Plate"]
    assert csv_report["candidate_roles"]["well"] == ["Well"]
    assert csv_report["candidate_roles"]["compound"] == ["Compound"]
    assert csv_report["candidate_roles"]["concentration"] == ["Dose"]
    assert csv_report["sample_rows"][1]["Compound"] == "Drug A"

    builder = report["python_builders"][0]
    assert builder["imports"] == ["csv"]
    assert builder["functions"] == ["build"]
    assert len(builder["sha256"]) == 64
    assert report["errors"] == []


def test_parquet_dependency_error_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "metadata.parquet").write_bytes(b"PAR1not-a-real-parquet")

    def unavailable() -> tuple[object, object, object]:
        raise inspect_timed_metadata.ParquetDependencyError("install pyarrow")

    monkeypatch.setattr(inspect_timed_metadata, "_load_pyarrow", unavailable)
    report = inspect_timed_metadata.inspect_metadata(tmp_path)

    assert report["parquet"][0]["errors"] == ["install pyarrow"]
    assert report["errors"] == ["metadata.parquet: install pyarrow"]


def test_parquet_report_includes_schema_roles_and_values(tmp_path: Path) -> None:
    pyarrow = pytest.importorskip("pyarrow")
    parquet = pytest.importorskip("pyarrow.parquet")
    table = pyarrow.table(
        {
            "plate_barcode": ["P1", "P1", "P2"],
            "well": ["A01", "A02", "B01"],
            "compound": ["DMSO", "Drug A", "Drug A"],
            "concentration_um": [0.0, 1.0, 2.0],
            "access_token": ["one", "two", "three"],
        }
    )
    parquet.write_table(table, tmp_path / "metadata.parquet")

    report = inspect_timed_metadata.inspect_metadata(tmp_path)
    parquet_report = report["parquet"][0]
    columns = {column["name"]: column for column in parquet_report["columns"]}

    assert parquet_report["row_count"] == 3
    assert parquet_report["sample_complete"] is True
    assert parquet_report["candidate_roles"]["plate"] == ["plate_barcode"]
    assert parquet_report["candidate_roles"]["well"] == ["well"]
    assert parquet_report["candidate_roles"]["compound"] == ["compound"]
    assert parquet_report["candidate_roles"]["concentration"] == ["concentration_um"]
    assert columns["compound"]["distinct_value_count"] == 2
    assert columns["concentration_um"]["minimum"] == 0.0
    assert columns["concentration_um"]["maximum"] == 2.0
    assert columns["access_token"]["redacted"] is True
    assert parquet_report["sample_rows"][0]["access_token"] == "<redacted>"
    assert report["errors"] == []


def test_cli_emits_one_json_document(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_json(tmp_path / "acquisition" / "plate.json", {"plate": "P1"})

    result = inspect_timed_metadata.main(["--quiet", str(tmp_path)])

    captured = capsys.readouterr()
    assert result == 0
    assert captured.err == ""
    assert json.loads(captured.out)["json"]["documents"][0]["path"] == ("acquisition/plate.json")


def test_cli_output_file_is_excluded_from_inspection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_json(tmp_path / "acquisition" / "plate.json", {"plate": "P1"})
    output = tmp_path / "inspection.json"
    output.write_text("stale output", encoding="utf-8")

    result = inspect_timed_metadata.main(["--quiet", "--output", str(output), str(tmp_path)])

    captured = capsys.readouterr()
    assert result == 0
    assert captured.out == ""
    assert captured.err == ""
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["file_counts"]["json"] == 1
    assert report["json"]["documents"][0]["path"] == "acquisition/plate.json"


@pytest.mark.parametrize("value", ["0", "-1"])
def test_cli_rejects_nonpositive_limits(value: str) -> None:
    with pytest.raises(SystemExit):
        inspect_timed_metadata.main(["--max-values", value, "."])

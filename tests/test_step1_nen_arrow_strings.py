import pandas as pd
import pytest

from model.part2.step1_NEN import (
    _arrow_strings_to_objects,
    _normalise_text_series,
)


def test_python_string_columns_are_detached_to_object_dtype():
    frame = pd.DataFrame(
        {
            "identifier": pd.Series(
                ["alpha", pd.NA, "beta"],
                dtype=pd.StringDtype(storage="python"),
            ),
            "number": [1, 2, 3],
        }
    )

    result = _arrow_strings_to_objects(frame)

    assert result["identifier"].dtype == object
    assert result["identifier"].iloc[0] == "alpha"
    assert pd.isna(result["identifier"].iloc[1])
    assert result["identifier"].iloc[2] == "beta"
    assert result["number"].tolist() == [1, 2, 3]


def test_pyarrow_string_columns_are_detached_to_object_dtype():
    pytest.importorskip("pyarrow")
    frame = pd.DataFrame(
        {
            "identifier": pd.Series(
                ["alpha", pd.NA, "beta"],
                dtype=pd.StringDtype(storage="pyarrow"),
            )
        }
    )

    result = _arrow_strings_to_objects(frame)

    assert result["identifier"].dtype == object
    assert result["identifier"].tolist() == ["alpha", None, "beta"]


def test_normalise_text_series_uses_object_dtype_and_preserves_missing_values():
    source = pd.Series(
        [" Alpha ", pd.NA, None, 123],
        name="identifier",
        dtype=object,
    )

    result = _normalise_text_series(source, lowercase=True)

    assert result.dtype == object
    assert result.iloc[0] == "alpha"
    assert pd.isna(result.iloc[1])
    assert pd.isna(result.iloc[2])
    assert result.iloc[3] == "123"


def test_normalise_pyarrow_text_series_without_astype_conversion():
    pytest.importorskip("pyarrow")
    source = pd.Series(
        [" Alpha ", pd.NA, " BETA "],
        name="identifier",
        dtype=pd.StringDtype(storage="pyarrow"),
    )

    result = _normalise_text_series(source, lowercase=True)

    assert result.dtype == object
    assert result.tolist() == ["alpha", pd.NA, "beta"]

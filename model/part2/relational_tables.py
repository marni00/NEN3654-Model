"""Read and write normalized non-spatial tables in pipeline GeoPackages."""

from pathlib import Path
import sqlite3

import pandas as pd


def write_table(path, table_name, dataframe, if_exists="replace"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        dataframe.to_sql(
            table_name,
            connection,
            if_exists=if_exists,
            index=False,
            chunksize=10_000,
        )


def read_table(path, table_name, **read_sql_kwargs):
    path = Path(path)
    with sqlite3.connect(path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        if exists is None:
            raise ValueError(f"Table '{table_name}' not found in {path}.")
        return pd.read_sql_query(
            f'SELECT * FROM "{table_name}"',
            connection,
            **read_sql_kwargs,
        )


def table_exists(path, table_name):
    path = Path(path)
    if not path.exists():
        return False
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone() is not None

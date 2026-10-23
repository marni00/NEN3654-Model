import sqlite3

import pytest

from model.id_lineage import (
    _suspended_layer_triggers,
    assign_persistent_layer_ids,
    validate_temp9_ids,
)


def _create_minimal_temp9(path):
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE gpkg_contents (
                table_name TEXT PRIMARY KEY,
                data_type TEXT NOT NULL
            );

            CREATE TABLE Waterleiding (
                fid INTEGER PRIMARY KEY,
                geom BLOB,
                name TEXT
            );
            INSERT INTO Waterleiding (fid, geom, name)
            VALUES (1, X'01', 'first'), (8, X'02', 'second');

            CREATE TABLE ThermischePijpleiding (
                fid INTEGER PRIMARY KEY,
                geom BLOB,
                name TEXT
            );
            INSERT INTO ThermischePijpleiding (fid, geom, name)
            VALUES (1, X'03', 'other layer');

            INSERT INTO gpkg_contents (table_name, data_type)
            VALUES
                ('Waterleiding', 'features'),
                ('ThermischePijpleiding', 'features');

            CREATE TRIGGER rtree_Waterleiding_geom_update
            AFTER UPDATE ON Waterleiding
            WHEN ST_IsEmpty(NEW.geom) = 0
            BEGIN
                SELECT 1;
            END;
            """
        )


def test_id_equals_native_temp9_fid_per_layer(tmp_path):
    gpkg_path = tmp_path / "KLIC_temp9.gpkg"
    _create_minimal_temp9(gpkg_path)

    counts = assign_persistent_layer_ids(gpkg_path)

    assert counts == {
        "ThermischePijpleiding": 1,
        "Waterleiding": 2,
    }

    with sqlite3.connect(gpkg_path) as connection:
        water = connection.execute(
            "SELECT fid, id FROM Waterleiding ORDER BY fid"
        ).fetchall()
        thermal = connection.execute(
            "SELECT fid, id FROM ThermischePijpleiding ORDER BY fid"
        ).fetchall()

    assert water == [(1, 1), (8, 8)]
    assert thermal == [(1, 1)]
    validate_temp9_ids(gpkg_path)


def test_assignment_restores_spatial_trigger(tmp_path):
    gpkg_path = tmp_path / "KLIC_temp9.gpkg"
    _create_minimal_temp9(gpkg_path)

    assign_persistent_layer_ids(gpkg_path)

    with sqlite3.connect(gpkg_path) as connection:
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM sqlite_master
            WHERE type = 'trigger'
              AND name = 'rtree_Waterleiding_geom_update'
            """
        ).fetchone() == (1,)

        with pytest.raises(sqlite3.OperationalError, match="ST_IsEmpty"):
            connection.execute(
                "UPDATE Waterleiding SET name = 'changed' WHERE fid = 1"
            )


def test_suspended_layer_triggers_allows_attribute_update_and_restores_trigger():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE features (
            fid INTEGER PRIMARY KEY,
            geom BLOB,
            id INTEGER
        );
        INSERT INTO features (fid, geom) VALUES (1, X'01');
        CREATE TRIGGER rtree_features_geom_update
        AFTER UPDATE ON features
        WHEN ST_IsEmpty(NEW.geom) = 0
        BEGIN
            SELECT 1;
        END;
        """
    )

    with pytest.raises(sqlite3.OperationalError, match="ST_IsEmpty"):
        connection.execute("UPDATE features SET id = 1 WHERE fid = 1")
    connection.rollback()

    connection.execute("BEGIN IMMEDIATE")
    with _suspended_layer_triggers(connection, "features"):
        connection.execute("UPDATE features SET id = 1 WHERE fid = 1")
    connection.commit()

    assert connection.execute(
        "SELECT id FROM features WHERE fid = 1"
    ).fetchone() == (1,)
    assert connection.execute(
        """
        SELECT COUNT(*)
        FROM sqlite_master
        WHERE type = 'trigger'
          AND name = 'rtree_features_geom_update'
        """
    ).fetchone() == (1,)

    connection.close()

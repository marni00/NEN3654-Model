#Script 1

# pipelines/rewrite_gdb_to_gpkg.py

from pathlib import Path

import pyogrio


def clean_layer_name(layer_name: str) -> str:
    """
    Remove the technical 'main.' schema prefix from a layer name.

    Examples:
        main.Waterleiding -> Waterleiding
        Waterleiding      -> Waterleiding
    """
    return layer_name.removeprefix("main.")


def rewrite_gdb_to_gpkg(
    input_path: Path,
    output_path: Path
) -> Path:
    """
    Read all layers from the input datasource, harmonize their
    column ordering, and write them to a GeoPackage.

    Output layer names have the optional 'main.' prefix removed.
    """

    # ------------------------------------------------------------
    # 1) Get all source layer names
    # ------------------------------------------------------------
    layers = [
        name
        for name, _geom_type in pyogrio.list_layers(input_path)
    ]

    if not layers:
        raise ValueError(
            f"No layers found in input datasource: {input_path}"
        )

    # Check that cleaned output names will remain unique.
    output_layer_names = [
        clean_layer_name(layer)
        for layer in layers
    ]

    if len(output_layer_names) != len(set(output_layer_names)):
        raise ValueError(
            "Cleaning layer names by removing 'main.' would create "
            "duplicate output layer names."
        )

    reference_layer = layers[0]

    # ------------------------------------------------------------
    # 2) Read column names per layer
    # ------------------------------------------------------------
    columns_by_layer = {}

    for layer in layers:
        info = pyogrio.read_info(
            input_path,
            layer=layer
        )

        columns_by_layer[layer] = list(info["fields"])

    # ------------------------------------------------------------
    # 3) Find columns shared by every layer
    # ------------------------------------------------------------
    common_cols = sorted(
        set.intersection(
            *[
                set(columns_by_layer[layer])
                for layer in layers
            ]
        )
    )

    # ------------------------------------------------------------
    # 4) Use the reference layer's order for shared columns
    # ------------------------------------------------------------
    reference_cols = columns_by_layer[reference_layer]

    shared_order = [
        column
        for column in reference_cols
        if column in common_cols
    ]

    # Normally this should not add anything, but keeps it robust.
    shared_order += [
        column
        for column in common_cols
        if column not in shared_order
    ]

    # ------------------------------------------------------------
    # 5) Read and order all layers
    # ------------------------------------------------------------
    dfs = {}

    for layer in layers:
        layer_cols = columns_by_layer[layer]

        unique_part = [
            column
            for column in layer_cols
            if column not in common_cols
        ]

        ordered_cols = shared_order + unique_part

        df = pyogrio.read_dataframe(
            input_path,
            layer=layer,
            columns=ordered_cols,
            read_geometry=True
        )

        geom_col = df.geometry.name

        # Keep attributes in the intended order, followed by geometry.
        final_cols = ordered_cols.copy()

        if geom_col in df.columns:
            final_cols.append(geom_col)

        df = df[final_cols]

        dfs[layer] = df

    # ------------------------------------------------------------
    # 6) Write output with cleaned layer names
    # ------------------------------------------------------------
    if output_path.exists():
        output_path.unlink()

    for source_layer, df in dfs.items():
        target_layer = clean_layer_name(source_layer)

        pyogrio.write_dataframe(
            df,
            output_path,
            layer=target_layer,
            driver="GPKG"
        )

        print(
            f"Wrote layer: {source_layer} "
            f"-> {target_layer}"
        )

    print(f"Wrote GeoPackage to: {output_path}")

    return output_path
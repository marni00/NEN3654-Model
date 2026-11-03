# NEN 3654 GIS Model

A semi-automated GIS workflow for preparing KLIC data and executing the spatial risk assessments for mutual influence between high-votlage systems and conduits, following the NEN 3654.

The model combines feature attribute- and spatial based data preparation, spatial filtering, geometry based feature segmentation, detection of parallism, normative distance and length calculations and NEN 3654 assessment step 1 (and step 2 for inductive influence). A PySide6 desktop application provides an interactive interface for using the model, including: selecting infrastructure, reviewing features and assigning electricity-cable groups.

**Important:** This model supports GIS-based assessments of four influence types of the NEN3654. Outputs must be reviewed extensively. The model implementation does not certify compliance or establish that the implemented formulas, thresholds and model interpretations are valid for every project. 

## 1. Workflow

The workflow is divided into three main parts. Part 1 prepares and filters the KLIC data. Part 2 filters the remaining feature population based ontheir technical properties and spatial relevance for the norm. Part 3 analyses sthe pipeline and hv-system relations and performs the NEN 3654 assessments.

```text
KLIC FileGDB / GeoPackage
          |
          v
   PART 1 — DATA PREPARATION
   - Harmonise attributes and units
   - Standardise utility layers
   - Remove duplicate / covered features
   - Combine compatible conduit and electricity features
   - Apply property and spatial relevance filters
          |
          v
      KLIC_temp9.gpkg
          |
          v
   PART 2 — FILTER ANALYSIS POPULATION
   - Select conduits and relevant influencers
   - Segment geometries and identify parallel pairs
   - Calculate distances and construct reference runs
   - Calculate a and l values
          |
          v
   PART 3 — NEN 3654 STEP 1
   - Assess inductive, thermal, capacitive and resistive effects
          |
          v
      KLIC_temp19.gpkg
          |
          v
   USER ASSIGNS CABLE CODES
          |
          v
   PART 3 — NEN 3654 STEP 2
   - Retrieve K-values from Step2_Table.csv
   - Calculate UC values for the eligible population (inductive only)
          |
          v
      KLIC_temp20.gpkg
          |
          v
   POST-PROCESSING / GIS EXPORTS / OPTIONAL ONLINE UPLOAD
```

### Part 1 — Cleaning, standardizing and preparation

Part 1 converts the supplied File Geodatabase (`.gdb`) or GeoPackage (`.gpkg`) into a consistent working dataset. It:

1. Copies the source layers into a GeoPackage and normalises the layer structure.
2. Harmonises pipe diameters, material descriptions and conductivity attributes.
3. Routes utility records into standard infrastructure layers and normalises vertical-position values.
4. Removes topologically duplicate or almost fully covered features.
5. Combines connected conduit features when their relevant properties are compatible.
6. Optionally adds a proposed high-voltage dataset.

### Part 2 — Filtering and preparation
1. The user selects pipelinefeature feature(s) of interest for the assessment. The model then identifies high-voltage influencers that may affect those selected conduits.
2. Filters features using voltage, conductivity and pipe-diameter properties.
3. Applies a first spatial NEN influence-zone screen and assigns inductive relevance flags.
4. Combines electricity features and assigns persistent feature IDs.
5. The model segments features based on the spatial relation between pipeline and hv-system segments, identifies accepted parallel relationships, calculates endpoint-to-influencer distances and groups the relationships into reference runs and influenced pipeline memberships. It then constructs sections between consecutive distance observations and calculates:

- **a** — the length-weighted average distance between the influenced conduit and the relevant high-voltage system
- **l** — the total accepted parallel length represented by the reference sections.

### Part 3 — NEN 3654 Step 1

These values **a** and **l** are used in the implemented NEN 3654 Step 1 assessment. The model assesses inductive, thermal, capacitive and resistive effects and stores the assessment results.

For all relevant pipeline and high-voltage system relations, four different spatial criteria, depending on the influence type, are checked for possible risks of intolerable mutual influence between the features.

Features that are at risk and thus are eligible for further assessment are placed in the *no-pass* population. Whether features receive a *pass* or *no-pass*  dependeds on the high-voltage influencer type and consequentially the weighted distance from the influenced pipeline and the total sum of parallel-run with that pipeline.

### Part 2 — NEN 3654 Step 2

After Step 1 has finished, the user assigns cable-group **Codes** to the electricity cables that remain in the Step 1 inductive *no-pass* population. These Codes are looked up in `utils/Step2_Table.csv`.

The model calculates the UC values for continuous, corrosion and single-phase short-circuit effect. A UC value of `1` or lower is classified as `Pass`; a value above `1` is classified as `No pass`.

Step 2 does not independently select a new population: it builds on the Step 1 inductive *no-pass* population. 

## 2. Getting started

### Requirements

The requires the following  Python libraries:

- **GeoPandas** — spatial data handling and GeoDataFrame operations.
- **Shapely** — geometry operations, segmentation, spatial predicates and distance calculations.
- **Pyogrio** — reading and writing GeoPackages and File Geodatabases.
- **Pandas** and **NumPy** — tabular processing and numerical calculations.
- **SQLite** — GeoPackage metadata and normalised non-spatial tables (Python's standard-library `sqlite3` module is used).
- **PySide6 / Qt WebEngine** — GUI.
- **MapLibre** — interactive map rendering in the GUI.
- **ArcGIS Python API** — optional; only required for portal publication.
- **pytest** — used by the project's automated tests.

### Create an environment

It is good practice to use a virtual environment in your python innal:

```powershell
python -3.12 -m venv nen3654_env
.\nen3654_env\Scripts\Activate.ps1
```

To run the model, the required dependencies must be installed in the activated virtual environment. This can again be done in the terminal(example):

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements-gui.txt
```

### Launch the GUI
Run the following command from the project root:

```powershell
python main_gui.py
```

The GUI is the recommended entry point for the interactive workflow. It validates the selected input KLIC and creates an isolated output directory.

## 3. Running an assessment

1. **Select the source data.** Choose a KLIC File Geodatabase (`.gdb`) or GeoPackage (`.gpkg`). Make sure the datasource is readable and contains the expected infrastructure layers and attributes.
2. **Configure Part 1.** Review the model settings. When applicable, enable the proposed-high-voltage input and provide its vector dataset.
3. **Run Part 1.** The model creates the prepared dataset `KLIC_temp9.gpkg` and the supporting intermediate files.
4. **Select the infrastructure for assessment.** Select the conduit features to include in Part 2. The model limits the electrical influencers to those spatially relevant to the selected conduits. Conduit features can be forcefully merged in this step too, when beiing sure features must belong together.
5. **Run NEN 3654 Step 1.** Review the selected population and the generated assessment results. The main Step 1 dataset is `KLIC_temp19.gpkg`.
6. **Assign Codes for Step 2.** After Step 1, assign Codes to the remaining electricity-cable features shown by the application. These Codes select the corresponding K-values from `utils/Step2_Table.csv`.
7. **Run NEN 3654 Step 2.** The model writes the Step 2 result to `KLIC_temp20.gpkg`.
8. **Review and export the results.** Use the generated output GeoPackages in the GUI or in a GIS application. Outputs can be uploaded to a personal ArcGIS Online space.

If the model is run, not from the GUI, but using the scripts themselves, their exact arguments and path settings must be checked manuelly. These are: `run_filtering.py`, `run_model.py` and `prepare_for_arcgis.py`

## 4. Configuration files

Relevant for when the model is run using the scripts and not from the GUI, otherwise irrelevant:

| File | Purpose |
|---|---|
| `config/config.toml` | General model settings, including the optional proposed-high-voltage input and electricity-cable Code assignments. |
| `config/selection.toml` | Selected conduit feature IDs and related selection/combine settings for Part 2. The GUI writes a run-specific version. |
| `config/symbology.json` | Symbology definitions used by the GUI and optional ArcGIS publication. |
| `utils/Step2_Table.csv` | Lookup table containing the Code-specific K-values used in Step 2. |

The GUI creates run-specific copies of `config.toml` and `selection.toml` in an isolated workspace.

## 5. Main outputs

The model produces intermediate datasets throughout the workflow. The following six GeoPackages are the main post-processed outputs intended for GIS review and publication:

| Output | Contents |
|---|---|
| `KLIC_raw_cleaned.gpkg` | Cleaned and prepared KLIC data, kept relatively close to the source data. |
| `KLIC_filtered.gpkg` | Features and relationships that remain relevant before the Step 1 assessment. |
| `KLIC_no_pass_step1.gpkg` | Features with one or more Step 1 no-pass results. |
| `KLIC_results_step1.gpkg` | Complete Step 1 assessment results, including pass and no-pass records. |
| `KLIC_no_pass_step2.gpkg` | Features receiving a Step 2 inductive no-pass result. |
| `KLIC_results_step2.gpkg` | Complete Step 2-enriched inductive population, based on the Step 1 inductive no-pass population. |

Step 2 outputs are generated only after Step 2 has been run. Intermediate GeoPackages and CSV files are also created; these support processing and preserve relationship information between physical features, segments and reference runs.

## 6. Important data and identifier concepts

The model uses several identifiers for different purposes. They should not be treated as interchangeable.

| Field | Meaning |
|---|---|
| `id` | Persistent identifier assigned after Part 1. Unique **within a layer** and used for GUI selection and cable-Code assignment. |
| `feature_id` | Working identifier for a feature in a processed/segmented layer. Equal values in different layers do not imply the same physical feature. |
| `part_no` | One-based segment number within a `feature_id`. |
| `run_id` | Identifier for a matched electrical reference run. |
| `influenced_run_id` | Text identifier in the form `<run_id>.<subrun_no>`, identifying a conduit membership within a reference run. |

In particular, `id` is not a GeoPackage FID and is not globally unique across all layers. 
The combination of `layer`+`id` is however globally unique.

## 7. Technical notes

- Spatial analysis is performed in **EPSG:28992**. The GUI converts display geometries to EPSG:4326 for map rendering; this display transformation does not change the model's analysis CRS.
- Proposed high-voltage geometry is optional and is controlled through the GUI or when running scripts seperately unsing: `config.toml`.
- The Step 1 inductive *no-pass* population is the only population eligible for Step 2.
- The Step 2 lookup table is a required part of the Step 2 workflow. Check that every assigned Code has the intended entry in `utils/Step2_Table.csv`.
- The GUI runs model work in a separate process and reports progress/errors through its worker interface.
- ArcGIS Online/Enterprise publication is optional. Portal login input is required, but is not stored.

## 8. Tests

The repository contains a `tests/` directory with regression tests 

```powershell
python -m pytest
```

The tests exercise code behaviour using made-up temporary data. Passing tests do not independently establish the scientific or regulatory validity of the implemented assessment rules.

## 9. Project structure

```text
.
├── main_gui.py                     # Desktop application entry point
├── run_filtering.py                # Part 1 workflow runner
├── run_model.py                    # Part 2 workflow runner
├── prepare_for_arcgis.py           # Post-processing/export runner
├── upload_ArcGIS_Online.py         # Optional publication wrapper
├── config/
│   ├── config.toml                 # Model configuration
│   ├── selection.toml              # Feature selection configuration
│   └── symbology.json              # Map and publication styling
├── model/
│   ├── part1/                      # KLIC preparation and filtering
│   ├── part2/                      # NEN 3654 analysis and assessments
│   ├── post/                       # GIS output preparation/publication
│   └── id_lineage.py               # Persistent ID management/validation
├── gui/                            # PySide6 GUI and supporting services
├── utils/
│   └── Step2_Table.csv             # Step 2 Code-to-K-value lookup
└── tests/                          # Automated regression tests
├── data/
    └── gui_runs                    # Location of intermediate and final outputs of gui model-runs
```

---

## Acknowledgement and maintenance

This README describes the workflow and project structure. For exact processing rules and per-script behaviour, see the model documentation and official NEN3654.

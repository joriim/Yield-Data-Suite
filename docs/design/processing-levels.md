# Yield Data Processing Levels and Fusion Architecture

Nov 3, 2026 · original design document (converted from .docx)

## Purpose and scope

A combine yield monitor does not measure yield. It measures a force on an impact plate, a moisture signal, a clock and a GNSS position. Each of these is captured at a different point in the machine and with its own lag. A trustworthy yield map exists only after these signals are converted to physical units, aligned in time and space, calibrated against weighed loads, cleaned, and aggregated to a grid. Remote sensing can then repair gaps and stripes, but it cannot replace the measurement.

This document defines that chain as **six processing levels (L0–L5)**, borrowing the logic of NASA EOSDIS levels for Earth observation data ([NASA Earthdata](https://www.earthdata.nasa.gov/learn/earth-observation-data-basics/data-processing-levels)). Every level has a defined input, model, output and uncertainty, so that any field-year can be reprocessed from raw data when a model improves. For each level the document gives the physical or statistical model with its equations, the tools that implement it, and a software architecture that runs the chain for many farms and years.

Scope: grain combines with impact-plate mass flow sensors (the most common type) and moisture sensors, ISO 11783 / ISOXML or vendor exports, Sentinel-2 and multispectral drone imagery (e.g. DJI Mavic 3M). Out of scope are yield prediction for fields without any yield data and the downstream uses of the map, such as carbon accounting.

## Processing levels L0–L5

The levels follow the EOSDIS logic: L0 is raw instrument data, L1 is time-referenced and converted to instrument units, L2 is derived variables at the original resolution and location, L3 is variables on a uniform grid, and L4 is model output from several measurements ([NASA Earthdata](https://www.earthdata.nasa.gov/learn/earth-observation-data-basics/data-processing-levels)). For yield data, L1 is split into decoding (L1A) and physical conversion (L1B), and multi-source fusion is added as L5.

| Level                                 | Content                                                                                                                                   | Main operations                                                                                   | Format                                            | Uncertainty driver                 |
|---------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------|---------------------------------------------------|------------------------------------|
| **L0** Raw log                        | Impact-plate force signal, moisture sensor signal, elevator speed, header up/down, GNSS sentences, controller timestamps, operator inputs | Archive as received; checksum                                                                     | Vendor binary, ISOXML TaskData + TimeLog binaries | —                                  |
| **L1A** Decoded                       | Per-sample records in SI units with one clock: force (N), moisture (%), ground speed (m/s), position (WGS84), swath (m), status flags     | Decode DDIs and vendor fields; unify clocks; drop communication artefacts                         | GeoParquet, one row per sample                    | Clock drift, missing samples       |
| **L1B** Mass flow                     | Wet and standard-moisture mass flow (kg/s) per sample, not yet located                                                                    | Tare, flow calibration curve, elevator-speed and pitch compensation, moisture standardisation     | GeoParquet                                        | Calibration curve, moisture sensor |
| **L2** Georeferenced yield            | Each record as a polygon with mass, effective area and yield (t/ha) at the true harvest position                                          | Flow delay and transfer function, antenna-to-header offset, distance, swath, overlap              | GeoParquet (polygons)                             | Delay, GNSS, swath                 |
| **L3** Calibrated, cleaned field-year | L2 reconciled to weighed loads, quality flags per record, cleaned subset                                                                  | Load reconciliation, per-machine factors, fill/finish removal, global and spatial outlier filters | GeoParquet + QA report                            | Remaining outliers, scale tickets  |
| **L4** Gridded yield map              | 10 m or 20 m raster with mass conserved, plus coverage, record count and spread layers                                                    | Mass apportioning from polygons to grid                                                           | Cloud-optimised GeoTIFF (multi-band)              | Support change, edge cells         |
| **L5** Fused and corrected map        | L4 with residual position shift removed, pass stripes levelled, gaps filled from imagery, zones                                           | Image registration, stripe model, regression kriging or RF gap fill, zoning                       | COG + GeoPackage (zones) + model card             | Model error in filled cells        |

Two practical notes. First, many vendor exports start at L1B or even L2, because the mass flow is already computed and the delay already applied in the display. Each record must therefore carry its entry level so that corrections are not applied twice. Second, ISO 11783 separates wet and dry quantities explicitly: DDI 87 is mass per time yield including the actual moisture (DDI 99), DDI 182 is the dry mass flow at the reference moisture set by DDI 184 ([isobus.net DDI 87](https://www.isobus.net/isobus/dDEntity/70), [DDI 182](https://www.isobus.net/isobus/dDEntity/208)).

## L0 → L1: from sensor signal to mass flow

### Impact plate physics

Grain thrown from the clean grain elevator hits a plate mounted on a load beam with strain gauges. The plate stops the horizontal motion of the grain, and the reaction force is proportional to the change of momentum, i.e. to the mass and the initial velocity of the grain ([US 10028435](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/10028435)). The original sensor patent averages the amplified force over about 1 s and converts it to mass flow through a flow calibration characteristic ([US 5343761](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/5343761)).

$$
F(t) \approx \dot m(t)\, v_1(t)\cos\theta + F_g(\alpha) + \varepsilon(t)
$$

Here ṁ is grain mass flow, v₁ the grain velocity leaving the elevator (set by elevator chain speed), θ the incidence angle, F_g(α) a gravity component that depends on machine pitch α, and ε the paddle-pulse noise. Forward pitch increases the sensor response and aft pitch reduces it, and elevator chain tension and paddle wear change the grain velocity and hence the force ([Wikipedia: Grain yield monitor](https://en.wikipedia.org/wiki/Grain_yield_monitor)).

### Signal conditioning

$$
\bar F_k = \frac{1}{T}\int_{t_k - T}^{t_k} \big(F(t) - F_0\big)\,dt, \qquad T \approx 1\ \text{s}
$$

F₀ is the tare: the signal with the elevator running empty, re-estimated from header-up periods in every load. The averaging window must cover several paddle passes so that the pulse train becomes a mean force.

### Flow calibration characteristic

The force-to-flow relation is non-linear, so a single-point calibration over- or underestimates between and beyond the calibration flow ([UNL EC2004](https://extensionpubs.unl.edu/publication/ec2004/na/pdf/view); [US 5369603](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/5369603)). A normalised form that also compensates elevator speed ω_e and pitch:

$$
\dot m_k = g\!\left(\frac{\bar F_k}{\omega_e/\omega_{\mathrm{ref}}}\right)\big(1 + \beta\,\alpha_k\big), \qquad g(x) = \sum_{j=1}^{J} a_j x^{j} \ \text{or piecewise linear}
$$

The coefficients a_j are fitted to L weighed calibration loads by matching the integrated mass of each load to its scale weight:

$$
\hat{\mathbf a} = \arg\min_{\mathbf a} \sum_{l=1}^{L} \left( M^{\mathrm{scale}}_l - \sum_{k \in l} \dot m_k(\mathbf a)\,\Delta t_k \right)^2
$$

Extension guidance is 3–6 loads of roughly 1.4–3.6 t (3,000–8,000 lb), each at a different but steady flow rate, obtained by varying speed or cut width ([SDSU P-00279](https://extension.sdstate.edu/sites/default/files/2023-10/P-00279.pdf); [MSU P4133](https://extension.msstate.edu/sites/default/files/document/2025-09/P4133_web.pdf)). A typical acceptance target after calibration is ±3 % per load ([John Deere S7 guide](https://support.koenigequipment.com/s7-combine-yield-monitor-calibration-guide)). Field checks show why this matters: calibration errors of 6–58 % were found in Mississippi combines in 2023–2024 ([MSU P4133](https://extension.msstate.edu/sites/default/files/document/2025-09/P4133_web.pdf)), and an uncalibrated monitor can be off by up to 100 % ([Farm Progress](https://www.farmprogress.com/farming-equipment/big-yield-monitor-errors-possible-if-left-uncalibrated)). A physics-based model of the plate with model-based estimation can also support self-calibration ([Reinke 2011](https://www.ideals.illinois.edu/items/27977)).

### Moisture and standard mass

Capacitive moisture sensors are generally reliable between about 10 and 33 % moisture ([Bayer](https://www.cropscience.bayer.us/articles/bayer/grain-yield-monitor-calibration)). Mass is converted to a reference moisture MC_ref (e.g. 14 % for cereals) or to dry matter:

$$
\dot m^{\mathrm{ref}}_k = \dot m_k\,\frac{100 - MC_k}{100 - MC_{\mathrm{ref}}}, \qquad \dot m^{\mathrm{DM}}_k = \dot m_k\,\frac{100 - MC_k}{100}
$$

This is the form of the UNL yield equation, which uses harvest and market moisture ([UNL EC2004](https://extensionpubs.unl.edu/publication/ec2004/na/pdf/view)). Values outside the sensor's valid range are flagged rather than used. The moisture signal has its own delay, which is handled at L2.

## L1 → L2: time alignment and georeferencing

The grain measured at time t was cut several seconds earlier and several metres back along the track. L2 places each record where the crop actually stood and converts it into a mass per area.

### Grain transport model

The combine acts as a dynamic system with respect to grain flow. Searcy et al. (1989) and later work modelled it as a transport delay, and Whelan and McBratney (1997) showed that the flow seen by the sensor is a strongly smoothed representation of the true yield ([Sensors 8:952, 2008](https://doi.org/10.3390/s8020952)). A first-order-plus-dead-time transfer function captures both effects:

$$
\dot m_{\mathrm{sensor}}(t) = (h * \dot m_{\mathrm{header}})(t), \qquad H(s) = \frac{e^{-\tau_d s}}{1 + \tau_c s}
$$

τ_d is the dead time (header to sensor) and τ_c the mixing time constant. In practice most pipelines only shift by τ_d. Deconvolution with τ_c recovers some sharpness but amplifies noise, so at 10–20 m output resolution a plain shift is normally sufficient.

### Estimating the delay

Reported delays differ by machine and crop: about 18 s for oats on a Massey Ferguson with a Flowcontrol monitor ([Thünen](https://literatur.thuenen.de/digbib_extern/zi028443.pdf)) and typically about 12 s for maize systems ([UNL BMPs](https://cropwatch.unl.edu/sites/unl.edu.ianr.extension.cropwatch/files/media/file/Presentation-Yield-Monitoring-BMPs.pdf)). Delay can also vary within a field, and estimation methods include matching head entry and exit with the start and stop of flow (Birrell et al. 1996; Nolan et al. 1996) and operator switch timing (Chosa et al. 2000), as reviewed by USDA-ARS ([ARS](https://ars.usda.gov/ARSUserFiles/50701000/cswq-0036-128359.pdf)). A data-driven estimator that needs no extra hardware:

$$
\hat\tau_d = \arg\min_{\tau} \sum_{(i,j) \in \mathcal N} \big( Y_i(\tau) - Y_j(\tau) \big)^2
$$

where 𝒩 are pairs of records on adjacent passes driven in opposite directions within a small distance. A wrong delay shifts the two directions apart, so the discrepancy is minimal at the true delay. The same is run separately for the moisture delay τ_m. An automated flow-delay optimiser of this kind is in beta at Kansas State University ([Griffin, KSU](https://shiny.agmanager.info/yieldDev)).

### Position of the cut crop

The GNSS antenna sits on the cab roof, not at the header centre. With heading ψ and the offset (Δx forward, Δy lateral) from antenna to header centre:

$$
\mathbf p^{\mathrm{hdr}}_k = \mathbf p^{\mathrm{ant}}(t_k - \tau_d) + \mathbf R(\psi_k) \begin{bmatrix} \Delta x \\ \Delta y \end{bmatrix}
$$

Positions are projected to a metric CRS (ETRS-TM35FIN, EPSG:3067, in Finland) before any distance is computed. Records with a GNSS quality below RTK fix are kept but flagged. Without RTK, swath and speed estimates degrade, and automatic header width adjustment can misfire ([MSU P4133](https://extension.msstate.edu/sites/default/files/document/2025-09/P4133_web.pdf); [Farm Progress](https://www.farmprogress.com/corn/check-for-errors-before-using-yield-data)).

### Distance, swath, area and yield

Distance between records comes from successive positions or from v_k Δt_k. Each record becomes a rectangle of length d_k along heading ψ_k and width w_k (actual cut width, from section/header data or the operator). Its effective area excludes overlap with polygons already harvested:

$$
A^{\mathrm{eff}}_k = \Big| P_k \setminus \bigcup_{j < k} P_j \Big|, \qquad Y_k = 10 \cdot \frac{\dot m^{\mathrm{ref}}_k\,\Delta t_k}{A^{\mathrm{eff}}_k}\ \ [\mathrm{t\,ha^{-1}}]
$$

with ṁ in kg/s and area in m² (10 converts kg/m² to t/ha). For a full, non-overlapping swath this reduces to Y_k = 10 ṁ_k / (v_k w_k). The vehicle-polygon and overlap steps are implemented in the R package pacu (pa_make_vehicle_polygons, pa_adjust_obs_effective_area) ([pacu reference](https://www.rdocumentation.org/packages/pacu/versions/0.1.74)), and the RITAS algorithm treats records as overlapping, time-ordered areal units without deleting data ([Damiano & Niemi](https://arxiv.org/pdf/2209.11313)).

## L2 → L3: whole-field calibration and cleaning

L3 fixes two different things: the level of the field total (calibration) and the reliability of individual records (cleaning). They are kept separate, because a level error moves every record equally while a cleaning error removes or keeps specific records.

### Load reconciliation

Even a well-calibrated monitor drifts with crop, moisture and season. Every weighed quantity that can be linked to the field (calibration loads, grain-cart scale, trailer weighbridge tickets, dryer intake) gives a reconciliation factor per machine c and field-year:

$$
k_c = \frac{\sum_{l \in c} M^{\mathrm{scale}}_l}{\sum_{l \in c} \sum_{k \in l} \dot m^{\mathrm{ref}}_k\,\Delta t_k}, \qquad Y'_k = k_{c(k)}\, Y_k
$$

When only a field total is known, a single k is used for the field. Separate factors per combine remove most of the striping that appears when two machines share a field. Weighing during the harvest also lets the monitor be checked continuously against scales, but vehicle weight does not change simultaneously with the mass flow signal, which has to be accounted for ([US 10420278](https://patents.google.com/patent/US10420278)). The k values and their dates are stored in the machine registry so that drift becomes visible across seasons.

### Record filters

Each record gets a bitmask of quality flags instead of being deleted, so that filters can be changed and re-run. Most automated protocols remove about 20–40 % of points; Vega et al. (2019) removed about 30 % across 595 datasets, one third of it as spatial outliers ([CONICET](https://ri.conicet.gov.ar/handle/11336/126738)).

| Flag                       | Rule                                                                   | Source                                                                                                                  |
|----------------------------|------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------|
| Header up / not harvesting | Header status or zero swath                                            | ISOXML / vendor status                                                                                                  |
| Fill and finish            | First and last n seconds of each pass, n ≈ τ_d + τ_c                   | Flow ramps at pass ends ([MSU P4133](https://extension.msstate.edu/sites/default/files/document/2025-09/P4133_web.pdf)) |
| Speed                      | Outside machine limits, or abs(Δv / Δt) above a threshold              | Blackmore & Moore 1999; Yield Editor                                                                                    |
| Swath                      | Effective width below a fraction of header width (point rows, overlap) | pacu effective area                                                                                                     |
| Moisture                   | Outside the valid sensor range (about 10–33 %)                         | [Bayer](https://www.cropscience.bayer.us/articles/bayer/grain-yield-monitor-calibration)                                |
| GNSS                       | Quality worse than RTK fix, or position jump                           | Receiver status                                                                                                         |
| Field edge                 | Within a buffer of the field boundary                                  | Vega et al. 2019, paar::depurate                                                                                        |
| Global outlier             | Outside median ± 3·MAD (robust for skewed yields)                      | Vega et al. 2019; Arizo-García et al.                                                                                   |
| Local spatial outlier      | Significant negative local Moran's I                                   | Vega et al. 2019                                                                                                        |

The local Moran's I compares each record with its neighbours within a radius (Anselin 1995):

$$
I_i = \frac{z_i}{m_2} \sum_{j \ne i} w_{ij}\, z_j, \qquad z_i = Y_i - \bar Y, \quad m_2 = \frac{1}{n}\sum_i z_i^2
$$

A significantly negative I_i marks a high value surrounded by low ones, or the reverse, which in yield data is usually an artefact. The protocol is implemented in the R package paar (depurate) ([paar vignette](https://cran.r-project.org/web/packages/paar/vignettes/Yield_data.html)). Alternatives are Leroux et al. (2018) ([HAL](https://hal.inrae.fr/hal-02607448v1)), context-based data integrity zones that remove error-prone areas from metadata rather than single points ([Hawkins & Buckmaster 2023](https://elibrary.asabe.org/abstract.asp?aid=54393)), and USDA-ARS Yield Editor 2.0 as a reference tool ([Ag Data Commons](https://agdatacommons.nal.usda.gov/articles/model/Yield_Editor_2_0_7/24664038)).

Order matters: reconciliation (scale factor) first uses all harvested mass, including flagged records, because flagged records still carried real grain to the tank. Filters only decide which records may describe the spatial pattern.

## L3 → L4: gridded yield map

The default method is mass apportioning from record polygons to grid cells, not point interpolation. It conserves the field's total mass, keeps each record's own support, and needs no variogram. For cell g and the clean records i with polygons P_i:

$$
M_g = \sum_i m_i \frac{|P_i \cap g|}{|P_i|}, \qquad C_g = \sum_i |P_i \cap g|, \qquad Y_g = 10 \cdot \frac{M_g}{C_g}
$$

If all records are apportioned with effective (non-overlapping) polygons, the sum of M_g over all cells equals the reconciled field mass. When flagged records are left out of the pattern, their area shows up as reduced coverage instead of as wrong yield. This is the logic of pa_apportion_mass in pacu ([pacu reference](https://www.rdocumentation.org/packages/pacu/versions/0.1.74)). Ordinary or block kriging is the alternative when records are sparse, but it smooths short-range variation: in a row-level sugarcane study IDW and ordinary kriging lost variation that row-wise yield lines kept ([Scientia Agricola 2020](https://www.scielo.br/j/sa/a/Pgp57C8BZYYSDN5FrPQVSLL/?lang=en)).

### Grid definition

- **Resolution.** 20 m matches the Sentinel-2 red-edge and SWIR bands and comfortably exceeds the smoothing length of the grain flow. 10 m is possible with RTK and a short τ_c. Finer cells claim detail the signal no longer contains.

- **Origin.** Cells are snapped to a fixed origin (the Sentinel-2 tile grid in the UTM zone of the field under study — Finland spans zones 34–36, e.g. Seinäjoki is tile 34VEQ, EPSG:32634 — or a national EPSG:3067 grid) so that every year and every image falls on the same cells without resampling.

### Quality layers (bands of the same COG)

| Band          | Definition                                                        | Use                                     |
|---------------|-------------------------------------------------------------------|-----------------------------------------|
| yield         | Y_g (t/ha at MC_ref)                                              | The map                                 |
| coverage      | c_g = C_g / cell area                                             | Edge and gap cells (c_g \< 0.5 flagged) |
| n_records     | Number of contributing records                                    | Reliability                             |
| spread        | Area-weighted SD of Y_i within the cell                           | Local noise, mixed cells                |
| flagged_share | Share of the cell's harvested area that came from flagged records | Data quality                            |
| passes        | Number of distinct passes                                         | Detects stripe-prone cells              |

A pixel-level standard error follows from the spread and the effective number of records, inflated for the autocorrelation of consecutive records along the track (roughly n_eff ≈ number of passes, not number of records).

## L4 → L5: fusion with satellite and drone data

Imagery cannot measure yield, but canopy reflectance before harvest is correlated with it. L5 uses that correlation inside the same field and year, calibrated on reliable L4 cells, for four corrections: residual position shift, pass stripes, gaps, and zoning. Filled cells are always marked.

### Image inputs

- **Sentinel-2 L2A** surface reflectance (10 m and 20 m), cloud-masked, as a season time series. Vegetation indices or biophysical variables (LAI, fAPAR) from radiative-transfer models; LAI from RTM slightly outperformed plain indices for wheat yield ([Segarra et al. 2022](https://diposit.ub.edu/dspace/handle/2445/196721)).

- **Drone multispectral orthomosaics** (e.g. Mavic 3M), radiometrically calibrated to reflectance, aggregated to the same grid by area-weighted mean. Missing orthomosaic parts can be imputed from PlanetScope and Sentinel-2 ([IJGI 2023](https://www.mdpi.com/2220-9964/12/2/41/review_report)).

- **Cross-sensor harmonisation** before drone and satellite features are mixed: a per-band linear fit of drone reflectance aggregated to 20 m against concurrent Sentinel-2 reflectance.

- **Timing.** The index–yield relation is strongest in a specific window: 105–135 days after planting for maize ([Kayad et al. 2019](https://research.unipd.it/retrieve/e14fb26c-acd2-3de1-e053-1705fe0ac030/remotesensing-11-02873.pdf)). For wheat, multi-date series outperformed single images ([Segarra et al. 2022](https://diposit.ub.edu/dspace/handle/2445/196721)).

### Yield–image model

Fitted per field-year on cells with good coverage and no flags, with V_g the image feature vector:

$$
Y_g = f(\mathbf V_g) + r_g, \qquad f \in \{\text{linear},\ \text{random forest}\}
$$

Random forest on Sentinel-2 mapped within-field wheat yield at 10 m with an RMSE of 0.66 t/ha, 0.61 t/ha with environmental covariates ([Hunt et al. 2019](https://nora.nerc.ac.uk/id/eprint/525234/)). Relations differ between years, so a model must not be carried across years without recalibration ([Perich et al. 2023](https://ira.agroscope.ch/de-CH/publication/52331)).

### Correction 1: residual position shift

After the L2 delay correction, a remaining systematic along-track shift δ is estimated by maximising the correlation between the yield grid and the index grid:

$$
\hat{\boldsymbol\delta} = \arg\max_{\boldsymbol\delta \in \Delta}\ \mathrm{corr}\big( Y(\mathbf x),\ V(\mathbf x + \boldsymbol\delta) \big)
$$

with Δ restricted to the along-track direction and to a few cells. A clear, non-zero optimum feeds back as a correction to τ_d or the antenna offset in the machine registry, and L2–L4 are re-run. This step is a proposed method, validated on test fields before routine use; image-based rectification of position errors in machine maps is described in patent literature ([US 11877528](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11877528)).

### Correction 2: pass stripes

Different combines, calibrations or operators on adjacent passes produce stripes, and a per-pass gain is a known correction ([US 10373353](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/10373353)). A mixed model separates the spatial yield signal from a multiplicative pass effect, using the image prediction as the spatial reference:

$$
\log Y_i = \log f(\mathbf V_{g(i)}) + b_{p(i)} + \varepsilon_i, \qquad b_p \sim N(0, \sigma_b^2), \qquad Y_i^{*} = Y_i\, e^{-\hat b_{p(i)}}
$$

The gains are then renormalised so that the field total is unchanged.

### Correction 3: gap filling (regression kriging)

For cells with low coverage or only flagged records, the prediction combines the image model with kriged residuals of nearby reliable cells:

$$
\hat Y(\mathbf x_0) = f(\mathbf V(\mathbf x_0)) + \sum_{i} \lambda_i\, r(\mathbf x_i), \qquad \sigma^2(\mathbf x_0) = \sigma^2_f + \sigma^2_{\mathrm{OK}}(\mathbf x_0)
$$

Dobermann and Ping (2004) found that simple kriging with varying local means from a 4 m satellite image improved yield-map precision by nearly 20 % over ordinary kriging and predicted better where no reliable yield data existed ([Agron. J. 96:285](https://doi.org/10.2134/agronj2004.0285)). Karampoiki et al. (2021) replaced erroneous yield records with values from drone and Sentinel-2 data; agreement with hand-sampled biomass rose from R² 0.84 to 0.90 ([Precision Agriculture '21](https://reading-clone.eprints-hosting.org/103268)). Filled cells keep their own uncertainty band and are never re-used as training data for f.

### Correction 4: zones

Multi-year L5 maps are converted to relative yield per year, stacked with image history, apparent electrical conductivity and terrain, and clustered with spatial principal components and fuzzy k-means (KM-sPC). KM-sPC gave the most contiguous zones with the largest between-class yield differences ([Córdoba et al. 2013](https://ri.conicet.gov.ar/handle/11336/23303)); it is implemented as paar::kmspc following Córdoba et al. (2016) ([paar zone vignette](https://cran.r-project.org/web/packages/paar/vignettes/Zone_delineation.html)).

## Software architecture

<img src="media/image1.png" style="width:6in;height:3.1875in" alt="processing chain · 4 sources, 6 levels, 4 shared services" />

processing chain · 4 sources, 6 levels, 4 shared services

Each source enters at the level where it is needed: combine logs at L0, scale tickets at L3, imagery at L5. Every level reads its parameters from the machine registry and writes a versioned product to the catalog, so any field-year can be reprocessed from L0 when a parameter or model changes. This is the same reprocessing principle that makes EO data levels trustworthy.

### Components

| Component                        | Responsibility                                                                                                                                                           | Suggested implementation                                                                                                                                                                                                     |
|----------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Ingest adapters                  | Read ISOXML TaskData and TimeLogs, vendor CSV and shapefile exports, Ag Leader Advanced text; map DDIs and vendor fields to one schema; record each record's entry level | Python with [isoxml](https://pypi.org/project/isoxml); ADAPT plugins (.NET) where a vendor format needs them ([ADAPT](https://aggateway.atlassian.net/wiki/download/attachments/65568770/2016+ASABE+ADAPT+v3.pdf?version=1)) |
| L1 sensor module                 | Tare, flow calibration curve, elevator-speed and pitch compensation, moisture standardisation                                                                            | NumPy; parameters from the registry                                                                                                                                                                                          |
| L2 georeferencing                | Delay estimation and shift, antenna offset, vehicle polygons, effective area, yield                                                                                      | shapely 2 (vectorised GEOS), pyproj                                                                                                                                                                                          |
| L3 QA                            | Load reconciliation, flag rules, local Moran's I, QA report                                                                                                              | PySAL esda; R paar via rpy2 as reference implementation                                                                                                                                                                      |
| L4 gridding                      | Mass apportioning, quality bands                                                                                                                                         | geopandas overlay with the fixed grid, rasterio / rioxarray to COG                                                                                                                                                           |
| L5 fusion                        | Image retrieval and masking, harmonisation, yield–image model, shift, stripes, gap filling, zones                                                                        | openEO or STAC access to Copernicus Data Space; scikit-learn RF; gstools or PyKrige; statsmodels mixed models; paar kmspc                                                                                                    |
| Machine and calibration registry | Per machine and season: τ_d, τ_m, τ_c, antenna offset, header width, curve coefficients, k_c, validity dates                                                             | PostgreSQL tables, edited through the API                                                                                                                                                                                    |
| Catalog                          | One STAC item per product: level, parent items, parameter hash, code version, QA summary                                                                                 | pgSTAC                                                                                                                                                                                                                       |
| Storage                          | Immutable L0 objects, GeoParquet for L1–L3, COG for L4–L5, field boundaries                                                                                              | S3-compatible object store + PostGIS                                                                                                                                                                                         |
| Orchestration                    | One graph per field-year; re-materialises downstream products when data or parameters change                                                                             | Dagster (asset lineage) or Prefect                                                                                                                                                                                           |
| Access                           | Review and correction of parameters, map viewing, downloads                                                                                                              | FastAPI, OGC API Features and Tiles, QGIS                                                                                                                                                                                    |

### Data model

- **Field** (boundary, valid years) and **Machine** (sensor set, header widths) are master data.

- **MachineProfile** is versioned: delays, offsets, calibration curve, valid from–to. Every product records the profile version it used.

- **HarvestSession** links a machine, a field and a time window; **Load** links scale tickets to the records that filled them.

- **Record** (L1–L3 rows): time, position, GNSS quality, speed, heading, swath, force, elevator speed, moisture, wet and reference mass flow, applied delay, entry level, effective area, yield, flag bitmask, pass id.

- **Product** (L4–L5): raster bands as in the quality-layer table, plus a model card for L5 (features, fit statistics, filled-cell share).

### Design rules

1.  L0 is never modified; everything above it is derived and reproducible.

2.  Each record carries its entry level, so vendor-applied corrections are never applied twice.

3.  Records are flagged, never deleted.

4.  Mass conservation is an automated test: the sum of L4 cell masses must equal the reconciled field mass within a tolerance.

5.  All years and images share one grid origin.

6.  Imagery only corrects within the same field-year, and filled cells are labelled and kept out of model training.

## Validation and uncertainty budget

### Uncertainty budget

| Source                      | Magnitude reported                                                                                                                                                                                                                                                                                                    | What it biases                           | Control in the chain                                       |
|-----------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------|------------------------------------------------------------|
| Mass flow calibration level | Up to 100 % uncalibrated ([Farm Progress](https://www.farmprogress.com/farming-equipment/big-yield-monitor-errors-possible-if-left-uncalibrated)); 6–58 % found in field checks ([MSU P4133](https://extension.msstate.edu/sites/default/files/document/2025-09/P4133_web.pdf)); ±3 % per load after good calibration | Field total                              | Multi-load calibration (L1B), load reconciliation k_c (L3) |
| Calibration curve shape     | Bias between and beyond calibration points ([UNL EC2004](https://extensionpubs.unl.edu/publication/ec2004/na/pdf/view))                                                                                                                                                                                               | Flow-dependent: high and low yield areas | 3–6 loads across the flow range                            |
| Moisture                    | Sensors reliable between about 10 and 33 % ([Bayer](https://www.cropscience.bayer.us/articles/bayer/grain-yield-monitor-calibration))                                                                                                                                                                                 | Standard-moisture mass                   | Flag out-of-range; reference samples                       |
| Flow delay                  | About 12–18 s reported ([UNL](https://cropwatch.unl.edu/sites/unl.edu.ianr.extension.cropwatch/files/media/file/Presentation-Yield-Monitoring-BMPs.pdf), [Thünen](https://literatur.thuenen.de/digbib_extern/zi028443.pdf)); at 1.5 m/s that is 18–27 m of track                                                      | Position of patterns                     | Delay estimation (L2), residual shift check (L5)           |
| GNSS                        | RTK about 2.5 cm; SBAS 15–30 cm pass-to-pass ([ASA CCA guide](https://open-exam-prep.com/study-guides/asa-cca/precision-ag/gps-gis-remote-sensing))                                                                                                                                                                   | Swath, speed, area, overlap              | RTK; quality flags                                         |
| Gridding                    | Support change, partly covered edge cells                                                                                                                                                                                                                                                                             | Edge cells, short features               | Coverage and spread bands (L4)                             |
| Gap filling                 | Sentinel-2 RF studies report about 0.6–0.7 t/ha RMSE ([Hunt et al. 2019](https://nora.nerc.ac.uk/id/eprint/525234/))                                                                                                                                                                                                  | Filled cells only                        | Per-cell variance, filled-cell label                       |

### Validation steps

1.  **Per-load residuals.** Integrated mass vs. scale weight for every calibration load and reconciliation load, reported as percent error by flow rate.

2.  **Opposite-pass consistency.** The mean squared difference between adjacent passes in opposite directions should reach its minimum at the applied delay.

3.  **Known-gap test.** Mow strips of known position before harvest and check where the yield map shows zero yield; this is how PAMI tested position and mapping accuracy of three commercial monitors ([PAMI](https://pami.ca/pdfs/reports_research_updates/(7h)%20Miscellaneous/745.PDF)).

4.  **Mass conservation.** Automated check that L4 cell masses add up to the reconciled field mass.

5.  **Gap-fill cross-validation.** Hide reliable cells in spatial blocks (not at random, because neighbouring cells are autocorrelated), predict them with the L5 model and report RMSE and bias.

6.  **Independent ground truth.** Hand-harvested or biomass samples at zone-representative points, as used by Karampoiki et al. (2021) to compare raw and fused maps ([Precision Agriculture '21](https://reading-clone.eprints-hosting.org/103268)).

### Assumptions to confirm

- The pitch and elevator-speed compensation terms in the L1B model are proposed forms; their coefficients must be estimated from test data or taken from the manufacturer, and they may already be applied inside the monitor.

- Access to raw force and elevator-speed signals (true L0) varies by manufacturer; if only mass flow is exported, processing starts at L1B and the L1 models serve as documentation.

- The residual-shift and stripe models in L5 need validation on fields with known gaps before routine use.

## References

**Data levels and standards**

- NASA Earthdata. Data processing levels. [earthdata.nasa.gov](https://www.earthdata.nasa.gov/learn/earth-observation-data-basics/data-processing-levels)

- ISO 11783-11 Data Dictionary: DDI 87 Mass Per Time Yield; DDI 182 Dry Mass Per Time Yield; DDI 358, 359 average dry yield. [isobus.net DDI 87](https://www.isobus.net/isobus/dDEntity/70), [DDI 182](https://www.isobus.net/isobus/dDEntity/208), [DDI 359](https://www.isobus.net/isobus/dDEntity/454)

- isoxml (Python ISOXML library). [PyPI](https://pypi.org/project/isoxml) · Bullock, R. et al. (2016). ADAPT: Interoperability through an industry open-source toolkit. ASABE 2462264. [PDF](https://aggateway.atlassian.net/wiki/download/attachments/65568770/2016+ASABE+ADAPT+v3.pdf?version=1)

**Mass flow sensing and calibration**

- Method and apparatus for measuring grain mass flow rate in harvesters. US 5343761. [USPTO](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/5343761) · Calibration of a non-linear sensor. US 5369603. [USPTO](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/5369603) · Sensor calibration using field information. US 10028435. [USPTO](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/10028435) · Methods for monitoring yield and vehicle weight. US 10420278. [Google Patents](https://patents.google.com/patent/US10420278)

- Reinke, R.E. (2011). Self-calibrating mass flow sensor. MS thesis, University of Illinois. [IDEALS](https://www.ideals.illinois.edu/items/27977)

- Luck, J.D. et al. Yield data and avoiding errors during harvest. UNL Extension EC2004. [PDF](https://extensionpubs.unl.edu/publication/ec2004/na/pdf/view)

- SDSU Extension P-00279 (2023). Yield monitor accuracy and calibration. [PDF](https://extension.sdstate.edu/sites/default/files/2023-10/P-00279.pdf)

- Mississippi State University Extension P4133 (2025). Improving accuracy of yield maps through calibration and post-harvest data processing. [PDF](https://extension.msstate.edu/sites/default/files/document/2025-09/P4133_web.pdf)

- Bayer Crop Science (2023). Grain yield monitor calibration. [Bayer](https://www.cropscience.bayer.us/articles/bayer/grain-yield-monitor-calibration) · John Deere S7 yield monitor calibration guide. [Koenig Equipment](https://support.koenigequipment.com/s7-combine-yield-monitor-calibration-guide) · Farm Progress (2019). Big yield monitor errors possible if left uncalibrated. [Farm Progress](https://www.farmprogress.com/farming-equipment/big-yield-monitor-errors-possible-if-left-uncalibrated) · Grain yield monitor. [Wikipedia](https://en.wikipedia.org/wiki/Grain_yield_monitor)

**Grain transport, delay and positioning**

- Grain flow dynamics and transport delay in combines (review of Searcy et al. 1989; Whelan & McBratney 1997). *Sensors* 8: 952 (2008). https://doi.org/10.3390/s8020952

- USDA-ARS. Automated delay-time determination for yield monitor data. [ARS PDF](https://ars.usda.gov/ARSUserFiles/50701000/cswq-0036-128359.pdf)

- Time lag between cutting and yield measurement in oats. Thünen Institute. [PDF](https://literatur.thuenen.de/digbib_extern/zi028443.pdf) · UNL Yield monitoring BMPs. [PDF](https://cropwatch.unl.edu/sites/unl.edu.ianr.extension.cropwatch/files/media/file/Presentation-Yield-Monitoring-BMPs.pdf)

- Griffin, T. Automated yield monitor data cleaning tool (beta). Kansas State University. [Shiny](https://shiny.agmanager.info/yieldDev)

- Farm Progress. Check for errors before using yield data. [Farm Progress](https://www.farmprogress.com/corn/check-for-errors-before-using-yield-data) · GNSS accuracy WAAS vs RTK. [ASA CCA guide](https://open-exam-prep.com/study-guides/asa-cca/precision-ag/gps-gis-remote-sensing) · PAMI yield monitor test results. [PDF](https://pami.ca/pdfs/reports_research_updates/(7h)%20Miscellaneous/745.PDF)

**Areal processing, cleaning and gridding**

- dos Santos, C.L., Miguez, F.E. (2024). PACU: Precision agriculture computational utilities. *SoftwareX* 28: 101971. https://doi.org/10.1016/j.softx.2024.101971 ([reference](https://www.rdocumentation.org/packages/pacu/versions/0.1.74))

- Damiano, L., Niemi, J. The RITAS algorithm: a constructive yield monitor data processing algorithm. [arXiv:2209.11313](https://arxiv.org/pdf/2209.11313)

- Vega, A. et al. (2019). Protocol for automating error removal from yield maps. *Precision Agriculture* 20: 1030–1044. https://doi.org/10.1007/s11119-018-09632-8 · paar package. [Yield data vignette](https://cran.r-project.org/web/packages/paar/vignettes/Yield_data.html)

- Leroux, C. et al. (2018). A general method to filter out defective spatial observations from yield mapping datasets. *Precision Agriculture* 19: 789–808. [HAL](https://hal.inrae.fr/hal-02607448v1)

- Hawkins, E.M., Buckmaster, D.R. (2023). Improving yield data analysis using contextual data. *Appl. Eng. Agric.* 39(4): 391–398. [ASABE](https://elibrary.asabe.org/abstract.asp?aid=54393)

- Sudduth, K.A., Drummond, S.T. (2007). Yield Editor. *Agron. J.* 99: 1471–1482. Yield Editor 2.0.7: [Ag Data Commons](https://agdatacommons.nal.usda.gov/articles/model/Yield_Editor_2_0_7/24664038)

- Anselin, L. (1995). Local indicators of spatial association – LISA. *Geographical Analysis* 27(2): 93–115.

- Row-level yield lines vs. IDW and kriging. *Scientia Agricola* 77(5) (2020). [SciELO](https://www.scielo.br/j/sa/a/Pgp57C8BZYYSDN5FrPQVSLL/?lang=en)

**Fusion with satellite and drone data**

- Dobermann, A., Ping, J.L. (2004). Geostatistical integration of yield monitor data and remote sensing improves yield maps. *Agron. J.* 96(1): 285–297. https://doi.org/10.2134/agronj2004.0285

- Karampoiki, M. et al. (2021). Producing grain yield maps by merging combine harvester and remote sensing data. *Precision Agriculture '21*: 645–651. https://doi.org/10.3920/978-90-8686-916-9_77 ([Reading](https://reading-clone.eprints-hosting.org/103268))

- Hunt, M.L. et al. (2019). High resolution wheat yield mapping using Sentinel-2. *Remote Sens. Environ.* 233: 111410. https://doi.org/10.1016/j.rse.2019.111410

- Segarra, J., Araus, J.L., Kefauver, S.C. (2022). Sentinel-2 data to estimate within-field wheat grain yield. *Int. J. Appl. Earth Obs. Geoinf.* 107: 102697. https://doi.org/10.1016/j.jag.2022.102697

- Kayad, A. et al. (2019). Monitoring within-field variability of corn yield using Sentinel-2 and machine learning techniques. *Remote Sens.* 11: 2873. https://doi.org/10.3390/rs11232873

- Perich, G. et al. (2023). Pixel-based yield mapping and prediction from Sentinel-2 using spectral indices and neural networks. *Field Crops Res.* 292: 108824. https://doi.org/10.1016/j.fcr.2023.108824

- Imputation of missing parts in UAV orthomosaics using PlanetScope and Sentinel-2 data. *ISPRS Int. J. Geo-Inf.* 12(2): 41 (2023). https://doi.org/10.3390/ijgi12020041

- Patents: Crop yield data adjustments, [US 10373353](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/10373353); Map-based control system with position error rectification, [US 11877528](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11877528)

- Córdoba, M. et al. (2013). Subfield management class delineation using cluster analysis from spatial principal components of soil variables. *Comput. Electron. Agric.* [CONICET](https://ri.conicet.gov.ar/handle/11336/23303) · Córdoba, M. et al. (2016). Protocol for multivariate homogeneous zone delineation in precision agriculture. *Biosyst. Eng.* 143: 95–107. https://doi.org/10.1016/j.biosystemseng.2015.12.008 · [paar zone vignette](https://cran.r-project.org/web/packages/paar/vignettes/Zone_delineation.html)

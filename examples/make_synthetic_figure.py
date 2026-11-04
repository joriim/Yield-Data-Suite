"""Render the synthetic-field figure: truth, raw log, L4 map and Q4 protein.

Usage: python examples/make_synthetic_figure.py docs/media/synthetic-field.png
Needs matplotlib. Writes a working copy of the demo to ./fig.
"""

import json
import sys

import matplotlib
import numpy as np
import yaml

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from yieldsuite import synthetic  # noqa: E402
from yieldsuite.config import load_config  # noqa: E402
from yieldsuite.l4.io import read_bands  # noqa: E402
from yieldsuite.levels import Level  # noqa: E402
from yieldsuite.pipeline import run  # noqa: E402


def main(out_png: str) -> None:
    cfg_path = synthetic.write_demo("fig")
    field = synthetic.make_field(0)
    export, lab = synthetic.quality_export(field)
    export.to_csv("fig/gs.csv", index=False)
    lab.to_csv("fig/lab.csv", index=False)
    raw = yaml.safe_load(cfg_path.read_text())
    raw["quality"] = {
        "sources": [{"path": "gs.csv", "machine_id": "combine-a"}],
        "lab_samples": "lab.csv",
        "n_factor": 5.7,
    }
    cfg_path.write_text(yaml.safe_dump(raw))
    summary = run(load_config(cfg_path), Level.L4)
    if not summary.ok:
        raise SystemExit(summary.error)
    l4, grid, _ = read_bands(summary.outputs["L4"])
    q4, _, _ = read_bands(summary.outputs["Q4"], require_yield=False)

    x0, y0 = synthetic.X0, synthetic.Y0
    w, h = synthetic.FIELD_W, synthetic.FIELD_H
    xx, yy = np.meshgrid(np.linspace(x0, x0 + w, 241), np.linspace(y0, y0 + h, 161))
    truth = synthetic.true_yield(xx, yy)
    log = field.log
    on = log["Header"] == 1
    raw_yield = 10 * log["Mass_Flow_kg_s"] / (synthetic.SPEED * synthetic.HEADER_W)
    extent = [
        grid.xmin - x0,
        grid.xmin + grid.nx * grid.resolution - x0,
        grid.ymax - grid.ny * grid.resolution - y0,
        grid.ymax - y0,
    ]

    fig, ax = plt.subplots(1, 4, figsize=(17, 4.3), constrained_layout=True)
    kw = {"cmap": "RdYlGn", "vmin": 4.5, "vmax": 9.5}
    im = ax[0].imshow(truth, origin="lower", extent=[0, w, 0, h], **kw)
    ax[0].set_title("1. Todellinen sato (t/ha)")
    ax[1].scatter(
        log["Easting"][on] - x0,
        log["Northing"][on] - y0,
        c=raw_yield[on],
        s=2,
        cmap="RdYlGn",
        vmin=4.5,
        vmax=11,
    )
    ax[1].set_title("2. Puimurin raakaloki\n(viive 12 s, +7 %, 18 % kosteus, piikit)")
    ax[2].imshow(l4["yield"], origin="upper", extent=extent, **kw)
    ax[2].set_title("3. Ketjun L4-kartta 20 m (t/ha)\nRMSE 0,04 t/ha totuuteen")
    im3 = ax[3].imshow(q4["protein_pct_dm"], origin="upper", extent=extent, cmap="viridis")
    ax[3].set_title("4. Q4-proteiini (% ka.)\nGrainSense-tyyppinen analysaattori")
    for a in ax:
        a.set_xlim(-5, w + 5)
        a.set_ylim(-5, h + 5)
        a.set_aspect("equal")
        a.set_xlabel("m")
    fig.colorbar(im, ax=ax[:3], shrink=0.8, label="t/ha")
    fig.colorbar(im3, ax=ax[3], shrink=0.8, label="% ka.")
    fig.savefig(out_png, dpi=110)
    print(json.dumps({"k": summary.k_factors, "flagged_pct": summary.flagged_pct}))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "synthetic-field.png")

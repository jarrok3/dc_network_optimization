"""Calendar-adjusted empirical-Bayes demand model (Section 3.4).

Poisson posteriors use Gamma shape/rate. If adjusted Pearson dispersion exceeds
1.2, a Gamma-prior negative-binomial likelihood is integrated on a log-rate grid;
it is deliberately not labelled a conjugate Gamma posterior.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from calendar_effects import holiday_class, load_holidays
from project import DATA_DIR, DEMAND_DIR


@dataclass
class PlanningDays:
    label: str
    dates: list[date]
    weights: np.ndarray
    store_ids: list[str]
    demand: np.ndarray

    @classmethod
    def load(cls, path):
        with np.load(path, allow_pickle=False) as data:
            return cls(str(data["label"].item()), [date.fromisoformat(d) for d in data["dates"].tolist()],
                       data["weights"], data["store_ids"].tolist(), data["demand"])


def read_inputs(stores_path, history_path):
    stores = pd.read_csv(stores_path, dtype=str).fillna("")
    history = pd.read_csv(history_path, dtype={"store_id": str})
    for frame, required, name in ((stores, {"store_id", "store_type", "opened_on", "closed_on"}, "stores"),
                                  (history, {"store_id", "date", "cages_ordered"}, "demand_history")):
        if not required <= set(frame):
            raise ValueError(f"{name}: missing columns {required - set(frame)}")
    if stores.store_id.eq("").any() or stores.store_id.duplicated().any() or stores.store_type.eq("").any():
        raise ValueError("stores: missing/duplicate store_id or missing store_type")
    if history.empty or history.duplicated(["store_id", "date"]).any():
        raise ValueError("demand_history: empty table or duplicate store_id/date")
    if not history.store_id.isin(stores.store_id).all():
        raise ValueError("demand_history: unknown store_id")
    values = pd.to_numeric(history.cages_ordered, errors="raise").to_numpy(float)
    if not np.isfinite(values).all() or (values < 0).any() or (values != np.floor(values)).any():
        raise ValueError("demand_history.cages_ordered must be finite nonnegative integers")
    history["cages_ordered"] = values
    dates = [date.fromisoformat(d) for d in history.date]
    start, end = min(dates), max(dates)
    calendar = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    si = stores.set_index("store_id").index.get_indexer(history.store_id)
    di = np.array([(d - start).days for d in dates])
    counts = np.zeros((len(stores), len(calendar)))
    observed = np.zeros_like(counts, dtype=bool)
    counts[si, di] = values
    observed[si, di] = True
    opened = [date.fromisoformat(d) for d in stores.opened_on]
    closed = [date.fromisoformat(d) if d else None for d in stores.closed_on]
    for i, (a, b) in enumerate(zip(opened, closed)):
        if b is not None and b <= a:
            raise ValueError(f"stores {stores.store_id.iloc[i]}: closed_on must follow opened_on")
        expected = np.array([a <= d and (b is None or d < b) for d in calendar])
        if not np.array_equal(expected, observed[i]):
            raise ValueError(f"demand_history {stores.store_id.iloc[i]}: missing open-day record or record outside store lifetime; zeros must be explicit")
    return stores, calendar, counts, observed


class DemandModel:
    @classmethod
    def fit(cls, stores_path=DATA_DIR / "stores.csv", history_path=DATA_DIR / "demand_history.csv",
            holidays=None, min_history=300, prior_strength=1.0, dispersion_threshold=1.2):
        if min_history < 1 or prior_strength <= 0 or dispersion_threshold <= 1:
            raise ValueError("min_history and prior_strength must be positive; dispersion_threshold must exceed 1")
        stores, dates, y, observed = read_inputs(stores_path, history_path)
        holidays = load_holidays() if holidays is None else set(holidays)
        types = sorted(stores.store_type.unique())
        ti = np.array([types.index(t) for t in stores.store_type])
        wd = np.array([d.weekday() for d in dates])
        mo = np.array([d.month - 1 for d in dates])
        hc = np.array([holiday_class(d, holidays) for d in dates])
        days = observed.sum(axis=1)
        long = days >= min_history
        for g, name in enumerate(types):
            if ((ti == g) & long).sum() < 2:
                raise ValueError(f"store_type {name}: at least two stores with >= {min_history} history days required")
        ng = len(types)
        weekday, month, holiday = np.ones((ng, 7)), np.ones(12), np.ones(3)
        total = y.sum(axis=1)
        theta = np.divide(total, days, out=np.ones(len(stores)), where=days > 0)
        type_totals = np.array([y[(ti == g) & long].sum(axis=0) for g in range(ng)])
        iterations, converged = 0, False
        # Block-coordinate Poisson maximum likelihood, including a fixed effect per store.
        for iterations in range(1, 501):
            before = np.concatenate((weekday.ravel(), month, holiday))
            effect = weekday[:, wd] * month[mo] * holiday[hc]
            for g in range(ng):
                ix = (ti == g) & long
                exp = observed[ix] @ effect[g]
                theta[ix] = total[ix] / exp
            mass = np.array([(theta[(ti == g) & long, None] * observed[(ti == g) & long]).sum(axis=0) for g in range(ng)])
            for g in range(ng):
                num = np.bincount(wd, weights=type_totals[g], minlength=7)
                den = np.bincount(wd, weights=mass[g] * month[mo] * holiday[hc], minlength=7)
                weekday[g] = np.divide(num, den, out=np.ones(7), where=den > 0)
                weekday[g] /= weekday[g].mean()
            num = np.bincount(mo, weights=type_totals.sum(axis=0), minlength=12)
            den = np.bincount(mo, weights=(mass * weekday[:, wd]).sum(axis=0) * holiday[hc], minlength=12)
            month = np.divide(num, den, out=np.ones(12), where=den > 0)
            month /= month.mean()
            num = np.bincount(hc, weights=type_totals.sum(axis=0), minlength=3)
            den = np.bincount(hc, weights=(mass * weekday[:, wd]).sum(axis=0) * month[mo], minlength=3)
            holiday = np.divide(num, den, out=np.ones(3), where=den > 0)
            holiday /= holiday[0]
            after = np.concatenate((weekday.ravel(), month, holiday))
            if not np.isfinite(after).all() or (after <= 0).any():
                raise ValueError("calendar regression has zero/nonfinite effects; insufficient calendar coverage")
            if np.max(np.abs(np.log(after / before))) < 1e-8:
                converged = True
                break
        if not converged:
            raise ValueError("calendar Poisson fixed-effect regression failed to converge in 500 iterations")
        raw = weekday[:, wd] * month[mo] * holiday[hc]
        # Product, rather than just the separate factors, has calendar mean one by type.
        normalizers = raw.mean(axis=1)
        effect = raw / normalizers[:, None]
        exposure = np.array([observed[i] @ effect[ti[i]] for i in range(len(stores))])
        rates = np.divide(total, exposure, out=np.zeros_like(total), where=exposure > 0)
        priors = {}
        a, b = np.empty(len(stores)), np.empty(len(stores))
        for g, name in enumerate(types):
            ix = (ti == g) & long
            mu = float(rates[ix].mean())
            variance = max(float(np.var(rates[ix], ddof=1) - np.mean(rates[ix] / exposure[ix])), mu * mu / 1e6)
            if mu <= 0:
                raise ValueError(f"store_type {name}: zero mean; cannot estimate a Gamma prior")
            shape = mu * mu / variance * prior_strength
            rate = shape / mu
            priors[name] = {"a": shape, "b": rate, "mu": mu, "long_history_stores": int(ix.sum())}
            a[ti == g], b[ti == g] = shape, rate
        means = rates[:, None] * effect[ti]
        mask = observed & long[:, None]
        residual = (y - means) ** 2
        parameters = int(long.sum() + ng * 6 + len(set(mo)) - 1 + len(set(hc)) - 1)
        dof = max(int(mask.sum()) - parameters, 1)
        pearson = float(np.sum((residual / np.maximum(means, 1e-12))[mask]) / dof)
        alpha = max(0.0, float(np.sum((residual - means)[mask]) / np.sum((means * means)[mask])))
        model = cls()
        model.stores, model.types, model.type_index = stores, types, ti
        model.a, model.b = a + total, b + exposure
        model.grid, model.grid_prob = None, None
        model.metadata = {"version": 1, "likelihood": "poisson", "gamma_parameterization": "shape/rate",
                          "history_start": dates[0].isoformat(), "history_end": dates[-1].isoformat(),
                          "holidays": sorted(holidays), "holiday_source": "explicit dates (default matches synthetic generator)",
                          "weekday_by_type": {name: weekday[g].tolist() for g, name in enumerate(types)},
                          "month": month.tolist(), "holiday": holiday.tolist(),
                          "holiday_classes": ["ordinary", "pre_holiday", "holiday"],
                          "normalizer_by_type": {name: float(normalizers[g]) for g, name in enumerate(types)},
                          "priors": priors, "prior_strength": prior_strength, "min_history": min_history,
                          "calendar_iterations": iterations, "pearson_dispersion": pearson,
                          "dispersion_threshold": dispersion_threshold, "nb_alpha": alpha,
                          "input_sha256": {str(Path(p).name): hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in (stores_path, history_path)}}
        if pearson > dispersion_threshold:
            if alpha <= 0:
                raise ValueError("dispersion check failed but negative-binomial overdispersion could not be estimated")
            model.metadata["likelihood"] = "negative_binomial"
            model.metadata["posterior_integration"] = "512 adaptive equally spaced log-rate nodes; Gamma prior, NB likelihood"
            model.grid = np.empty((len(stores), 512))
            model.grid_prob = np.empty_like(model.grid)
            for i in range(len(stores)):
                e = effect[ti[i], observed[i]]
                counts = y[i, observed[i]]
                r = 1 / alpha
                # Adapt to narrow posteriors instead of losing mass on a wide grid.
                mode = np.log(max((a[i] + total[i]) / (b[i] + exposure[i]), 1e-12))
                for _ in range(100):
                    theta_mode = np.exp(mode)
                    mu_mode = theta_mode * e
                    gradient = a[i] - b[i] * theta_mode + np.sum(counts - (counts + r) * mu_mode / (r + mu_mode))
                    curvature = b[i] * theta_mode + np.sum((counts + r) * r * mu_mode / (r + mu_mode) ** 2)
                    step = np.clip(gradient / curvature, -1, 1)
                    mode += step
                    if abs(step) < 1e-10:
                        break
                def log_density(log_theta):
                    theta_value = np.exp(log_theta)
                    mu_value = theta_value * e
                    return (a[i] + total[i]) * log_theta - b[i] * theta_value - np.sum((counts + r) * np.log(r + mu_value))
                peak = log_density(mode)
                widths = [12 / np.sqrt(curvature)] * 2
                for side, sign in enumerate((-1, 1)):
                    for _ in range(40):
                        if log_density(mode + sign * widths[side]) < peak - 40:
                            break
                        widths[side] *= 1.5
                    else:
                        raise ValueError(f"posterior tail integration failed for {stores.store_id.iloc[i]}")
                grid = np.exp(np.linspace(mode - widths[0], mode + widths[1], 512))
                # Include the theta Jacobian because integration is over log(theta).
                logp = a[i] * np.log(grid) - b[i] * grid
                mu = grid[:, None] * e
                logp += (counts * np.log(np.maximum(mu, 1e-300)) - (counts + r) * np.log(r + mu)).sum(axis=1)
                prob = np.exp(logp - logp.max())
                prob /= prob.sum()
                if prob[:3].sum() + prob[-3:].sum() > 1e-5:
                    raise ValueError(f"posterior grid truncated for {stores.store_id.iloc[i]}")
                model.grid[i], model.grid_prob[i] = grid, prob
        model.metadata["observed_days"] = days.tolist()
        model.metadata["observed_cages"] = total.tolist()
        model.metadata["exposure"] = exposure.tolist()
        return model

    @property
    def active_idx(self):
        end = self.metadata["history_end"]
        return np.flatnonzero((self.stores.opened_on <= end) & ((self.stores.closed_on == "") | (self.stores.closed_on > end)))

    def posterior_mean(self):
        return self.a / self.b if self.grid is None else np.sum(self.grid * self.grid_prob, axis=1)

    def calendar_factors(self, dates):
        m = self.metadata
        holidays = set(m["holidays"])
        return np.array([[m["weekday_by_type"][t][d.weekday()] * m["month"][d.month - 1]
                          * m["holiday"][holiday_class(d, holidays)] / m["normalizer_by_type"][t] for t in self.types] for d in dates])

    def draw(self, dates, rng):
        ix = self.active_idx
        # One independent posterior rate draw per store and planning day, per Section 3.4.
        if self.grid is None:
            theta = rng.gamma(self.a[ix], 1 / self.b[ix], size=(len(dates), len(ix)))
        else:
            theta = np.array([[rng.choice(self.grid[i], p=self.grid_prob[i]) for i in ix] for _ in dates])
        mean = theta * self.calendar_factors(dates)[:, self.type_index[ix]]
        if self.metadata["likelihood"] == "negative_binomial":
            alpha = self.metadata["nb_alpha"]
            return rng.negative_binomial(1 / alpha, 1 / (1 + alpha * mean))
        return rng.poisson(mean)

    def save(self, directory=DEMAND_DIR):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "model.json").write_text(json.dumps(self.metadata, indent=2), encoding="utf-8")
        self.stores.to_csv(directory / "model_stores.csv", index=False)
        arrays = {"a": self.a, "b": self.b}
        if self.grid is not None:
            arrays.update(grid=self.grid, grid_prob=self.grid_prob)
        np.savez_compressed(directory / "posterior.npz", **arrays)
        pd.DataFrame({"store_id": self.stores.store_id, "store_type": self.stores.store_type,
                      "active": np.isin(np.arange(len(self.stores)), self.active_idx),
                      "observed_days": self.metadata["observed_days"], "observed_cages": self.metadata["observed_cages"],
                      "calendar_exposure": self.metadata["exposure"], "gamma_shape": self.a if self.grid is None else np.nan,
                      "gamma_rate": self.b if self.grid is None else np.nan, "posterior_mean": self.posterior_mean()}).to_csv(directory / "posterior.csv", index=False)

    @classmethod
    def load(cls, directory=DEMAND_DIR):
        directory = Path(directory)
        model = cls()
        model.metadata = json.loads((directory / "model.json").read_text(encoding="utf-8"))
        model.stores = pd.read_csv(directory / "model_stores.csv", dtype=str).fillna("")
        model.types = sorted(model.stores.store_type.unique())
        model.type_index = np.array([model.types.index(t) for t in model.stores.store_type])
        with np.load(directory / "posterior.npz", allow_pickle=False) as data:
            model.a, model.b = data["a"], data["b"]
            model.grid = data["grid"] if "grid" in data else None
            model.grid_prob = data["grid_prob"] if "grid_prob" in data else None
        return model


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stores", type=Path, default=DATA_DIR / "stores.csv")
    parser.add_argument("--history", type=Path, default=DATA_DIR / "demand_history.csv")
    parser.add_argument("--holidays", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEMAND_DIR)
    parser.add_argument("--min-history", type=int, default=300)
    parser.add_argument("--prior-strength", type=float, default=1.0)
    parser.add_argument("--dispersion-threshold", type=float, default=1.2)
    args = parser.parse_args(argv)
    model = DemandModel.fit(args.stores, args.history, load_holidays(args.holidays), args.min_history,
                            args.prior_strength, args.dispersion_threshold)
    model.save(args.output_dir)
    print(f"Fitted {len(model.stores)} stores ({len(model.active_idx)} active); likelihood={model.metadata['likelihood']}; Pearson dispersion={model.metadata['pearson_dispersion']:.4f}")


if __name__ == "__main__":
    main()

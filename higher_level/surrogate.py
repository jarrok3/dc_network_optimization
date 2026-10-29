"""Six-feature Matérn-5/2 ARD Gaussian process on log(E/G), with exact lognormal EI."""
import numpy as np
from scipy.linalg import cho_factor, cho_solve
from scipy.optimize import minimize
from scipy.special import ndtr


def matern52(a, b, length):
    r = np.sqrt(np.sum(((a[:, None, :] - b[None, :, :]) / length) ** 2, axis=2))
    t = np.sqrt(5) * r
    return (1 + t + t * t / 3) * np.exp(-t)


class GapGaussianProcess:
    def fit(self, x, y):
        self.x = np.asarray(x, float)
        y = np.asarray(y, float)
        if self.x.ndim != 2 or y.shape != (len(self.x),) or not len(y) or not np.isfinite(self.x).all() or not np.isfinite(y).all():
            raise ValueError("GP requires finite feature rows and log-gap targets")
        self.mean = float(y.mean())
        self.scale = max(float(y.std()), .05)
        target = (y - self.mean) / self.scale
        dim = self.x.shape[1]

        def nll(theta):
            K = np.exp(2 * theta[dim]) * matern52(self.x, self.x, np.exp(theta[:dim]))
            K += np.eye(len(y)) * (np.exp(2 * theta[-1]) + 1e-8)
            try:
                factor = cho_factor(K, lower=True, check_finite=False)
                alpha = cho_solve(factor, target, check_finite=False)
            except np.linalg.LinAlgError:
                return 1e30
            return float(.5 * target @ alpha + np.log(np.diag(factor[0])).sum() + len(y) * .5 * np.log(2 * np.pi))
        initial = np.r_[np.zeros(dim + 1), -5.0]
        fitted = minimize(nll, initial, method="L-BFGS-B", bounds=[(-2.3, 3)] * dim + [(-3, 3), (-10, -2)], options={"maxiter": 60})
        theta = fitted.x if np.isfinite(fitted.fun) else initial
        self.length, self.amplitude, self.noise = np.exp(theta[:dim]), np.exp(2 * theta[dim]), np.exp(2 * theta[-1])
        K = self.amplitude * matern52(self.x, self.x, self.length) + np.eye(len(y)) * (self.noise + 1e-8)
        self.factor = cho_factor(K, lower=True, check_finite=False)
        self.alpha = cho_solve(self.factor, target, check_finite=False)
        return self

    def predict(self, x):
        x = np.asarray(x, float)
        cross = self.amplitude * matern52(x, self.x, self.length)
        mean = self.mean + self.scale * (cross @ self.alpha)
        var = self.amplitude - np.sum(cross * cho_solve(self.factor, cross.T, check_finite=False).T, axis=1)
        return mean, self.scale * np.sqrt(np.maximum(var, 0))


def expected_improvement(G, mean, std, incumbent):
    G, mean, std = np.broadcast_arrays(np.asarray(G, float), np.asarray(mean, float), np.asarray(std, float))
    if (G <= 0).any() or incumbent <= 0 or (std < 0).any():
        raise ValueError("EI requires positive costs and nonnegative standard deviations")
    sigma = np.maximum(std, 1e-12)
    z = (np.log(incumbent / G) - mean) / sigma
    value = incumbent * ndtr(z) - G * np.exp(np.clip(mean + sigma * sigma / 2, -700, 700)) * ndtr(z - sigma)
    deterministic = np.maximum(0, incumbent - G * np.exp(np.clip(mean, -700, 700)))
    return np.maximum(0, np.where(std < 1e-10, deterministic, value))

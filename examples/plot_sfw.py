"""
Comparison of variants of Stochastic FW
===========================================

The problem solved in this case is a L1 constrained logistic regression
(sometimes referred to as sparse logistic regression).
"""

import copt as cp
import matplotlib.pyplot as plt
import numpy as np

# .. construct (random) dataset ..
n_samples, n_features = 1000, 200
np.random.seed(0)
X = np.random.randn(n_samples, n_features)
y = np.random.rand(n_samples)
max_iter = int(1e6)

# .. objective function and regularizer ..
f = cp.utils.LogLoss(X, y)
constraint = cp.utils.L1Ball(1.)


# .. callbacks to track progress ..
def fw_gap(x):
    _, grad = f.f_grad(x)
    return constraint.lmo(-grad, x)[0].dot(-grad)


class TraceGaps(cp.utils.Trace):
    def __init__(self, f=None, freq=1):
        super(TraceGaps, self).__init__(f, freq)
        self.trace_gaps = []

    def __call__(self, dl):
        if self._counter % self.freq == 0:
            self.trace_gaps.append(fw_gap(dl['x']))
        super(TraceGaps, self).__call__(dl)


freq = 1000

cb_sfw_SAG = TraceGaps(f, freq=freq)
cb_sfw_SAGA = TraceGaps(f, freq=freq)
cb_sfw_mokhtari = TraceGaps(f, freq=freq)
cb_sfw_lu_freund = TraceGaps(f, freq=freq)

# .. run the SFW algorithm ..
result_sfw_SAG = cp.randomized.minimize_sfw(
    f.partial_deriv,
    X,
    y,
    np.zeros(n_features),
    constraint.lmo,
    callback=cb_sfw_SAG,
    tol=0,
    max_iter=max_iter,
    variant='SAG'
)

result_sfw_SAGA = cp.randomized.minimize_sfw(
    f.partial_deriv,
    X,
    y,
    np.zeros(n_features),
    constraint.lmo,
    callback=cb_sfw_SAGA,
    tol=0,
    max_iter=max_iter,
    variant='SAGA'
)

result_sfw_mokhtari = cp.randomized.minimize_sfw(
    f.partial_deriv,
    X,
    y,
    np.zeros(n_features),
    constraint.lmo,
    callback=cb_sfw_mokhtari,
    tol=0,
    max_iter=max_iter,
    variant='MK'
)

result_sfw_lu_freund = cp.randomized.minimize_sfw(
    f.partial_deriv,
    X,
    y,
    np.zeros(n_features),
    constraint.lmo,
    callback=cb_sfw_lu_freund,
    tol=0,
    max_iter=max_iter,
    variant='LF'
)
# .. plot the result ..
max_gap = max(cb_sfw_SAG.trace_gaps[0],
              cb_sfw_mokhtari.trace_gaps[0],
              cb_sfw_lu_freund.trace_gaps[0],
              cb_sfw_SAGA.trace_gaps[0])

plt.title("Stochastic Frank-Wolfe")
plt.plot(freq * np.arange(len(cb_sfw_SAG.trace_gaps)), np.array(cb_sfw_SAG.trace_gaps) / max_gap, lw=4, label="SFW -- SAG, NDTELP (2020)")
plt.plot(freq * np.arange(len(cb_sfw_SAGA.trace_gaps)), np.array(cb_sfw_SAGA.trace_gaps) / max_gap, lw=4, label="SFW -- SAGA, NDTELP (2020)")
plt.plot(freq * np.arange(len(cb_sfw_mokhtari.trace_gaps)), np.array(cb_sfw_mokhtari.trace_gaps) / max_gap, lw=4, label='SFW -- Mokhtari et al. (2020)')
plt.plot(freq * np.arange(len(cb_sfw_lu_freund.trace_gaps)), np.array(cb_sfw_lu_freund.trace_gaps) / max_gap, lw=4, label='SFW -- Lu and Freund (2020)')
plt.ylabel("Relative FW gap", fontweight="bold")
plt.xlabel("number of gradient evaluations", fontweight="bold")
plt.yscale("log")
plt.xlim((0, max_iter))
plt.legend()
plt.grid()
plt.show()

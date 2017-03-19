import concurrent.futures
from datetime import datetime
import numpy as np
from scipy import sparse, optimize
from numba import njit
from copt import utils


def minimize_SAGA(
    f, g=None, x0=None, step_size: float=-1, n_jobs: int=1, max_iter=100,
    tol=1e-6, verbose=False, trace=False) -> optimize.OptimizeResult:
    """Stochastic average gradient augmented (SAGA) algorithm.

    The SAGA algorithm can solve optimization problems of the form

                       argmin_x f(x) + g(x)

    Parameters
    ----------
    f, g
        loss functions. g can be none

    x0: np.ndarray, optional
        Starting point for optimization.


    Returns
    -------
    opt
        The optimization result represented as a
        ``scipy.optimize.OptimizeResult`` object. Important attributes are:
        ``x`` the solution array, ``success`` a Boolean flag indicating if
        the optimizer exited successfully and ``message`` which describes
        the cause of the termination. See `scipy.optimize.OptimizeResult`
        for a description of other attributes.

    References
    ----------
    Defazio, Aaron, Francis Bach, and Simon Lacoste-Julien. "SAGA: A fast
    incremental gradient method with support for non-strongly convex composite
    objectives." Advances in Neural Information Processing Systems. 2014.

    Rémi Leblond, Fabian Pedregosa, Simon Lacoste-Julien. "ASAGA: Asynchronous
    parallel SAGA". Proceedings of the 20th International Conference on
    Artificial Intelligence and Statistics (AISTATS). 2017.
    """
    if x0 is None:
        x = np.zeros(f.n_features)
    else:
        x = np.ascontiguousarray(x0).copy()

    if step_size < 0:
        step_size = 1. / (3 * f.lipschitz_constant())

    if g is None:
        g = utils.DummyProx()

    success = False
    epoch_iteration = _factory_sparse_SAGA(f, g)
    n_samples, n_features = f.A.shape

    # .. memory terms ..
    memory_gradient = np.zeros(n_samples)
    gradient_average = np.zeros(n_features)

    start_time = datetime.now()
    if trace:
        trace_x = np.zeros((max_iter, n_features))
    else:
        trace_x = np.zeros((0, 0))
    stop_flag = np.zeros(1, dtype=np.bool)
    trace_func = []
    trace_time = []

    # .. iterate on epochs ..
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = []
        for job_id in range(n_jobs):
            futures.append(executor.submit(
                epoch_iteration,
                x, memory_gradient, gradient_average,
                step_size, max_iter, job_id, tol, stop_flag, trace, trace_x))
        concurrent.futures.wait(futures)

    n_iter, certificate = futures[0].result()
    if trace:
        delta = (datetime.now() - start_time).total_seconds()
        trace_time = np.linspace(0, delta, n_iter)
        if verbose:
            print('Computing trace')
        # .. compute function values ..
        trace_func = []
        for i in range(n_iter):
            # TODO: could be parallelized
            trace_func.append(f(trace_x[i]) + g(trace_x[i]))

    if certificate < tol:
        success = True

    return optimize.OptimizeResult(
        x=x, success=success, nit=n_iter, trace_func=trace_func, trace_time=trace_time,
        certificate=certificate)


@njit(nogil=True)
def _support_matrix(
        A_indices, A_indptr, g_blocks, n_blocks):
    """
    """
    if n_blocks == 1:
        # XXX FIXME do something smart
        pass
    BS_indices = np.zeros(A_indices.size, dtype=np.int64)
    BS_indptr = np.zeros(A_indptr.size, dtype=np.int64)
    seen_blocks = np.zeros(n_blocks, dtype=np.int64)
    BS_indptr[0] = 0
    counter_indptr = 0
    for i in range(A_indptr.size - 1):
        low = A_indptr[i]
        high = A_indptr[i + 1]
        for j in range(low, high):
            g_idx = g_blocks[A_indices[j]]
            if seen_blocks[g_idx] == 0:
                # if first time we encouter this block,
                # add to the index and mark as seen
                BS_indices[counter_indptr] = g_idx
                seen_blocks[g_idx] = 1
                counter_indptr += 1
        BS_indptr[i+1] = counter_indptr
        # cleanup
        for j in range(BS_indptr[i], counter_indptr):
            seen_blocks[BS_indices[j]] = 0
    BS_data = np.ones(counter_indptr)
    return BS_data, BS_indices[:counter_indptr], BS_indptr


def _factory_sparse_SAGA(f, g):

    A = sparse.csr_matrix(f.A)
    b = f.b
    f_alpha = f.alpha
    A_data = A.data
    A_indices = A.indices
    A_indptr = A.indptr
    n_samples, n_features = A.shape

    partial_gradient = f.partial_gradient_factory()
    prox = g.prox_factory()

    # .. compute the block support ..
    if g.is_separable:
        g_blocks = np.arange(n_features)
    else:
        raise NotImplementedError
    # g_blocks is a map from n_features -> n_features
    unique_blocks = np.unique(g_blocks)
    n_blocks = np.unique(g_blocks).size
    assert np.all(unique_blocks == np.arange(n_blocks))

    BS_data, BS_indices, BS_indptr = _support_matrix(
        A_indices, A_indptr, g_blocks, n_blocks)
    BS = sparse.csr_matrix((BS_data, BS_indices, BS_indptr), (n_samples, n_blocks))

    d = np.array(BS.sum(0), dtype=np.float).ravel()
    idx = (d != 0)
    d[idx] = n_samples / d[idx]
    d[~idx] = 0.

    @njit(nogil=True)
    def epoch_iteration_template(
            x, memory_gradient, gradient_average, step_size, max_iter, job_id,
            tol, stop_flag, trace, trace_x):

        # .. SAGA estimate of the gradient ..
        x_old = x.copy()
        cert = np.inf
        it = 0
        sample_indices = np.arange(n_samples)

        if job_id == 0 and trace:
            trace_x[0, :] = x

        # .. inner iteration ..
        for it in range(1, max_iter):
            np.random.shuffle(sample_indices)
            for i in sample_indices:
                p = 0.
                for j in range(A_indptr[i], A_indptr[i+1]):
                    j_idx = A_indices[j]
                    p += x[j_idx] * A_data[j]

                grad_i = partial_gradient(p, b[i])
                mem_i = memory_gradient[i]

                # .. update coefficients ..
                for j in range(A_indptr[i], A_indptr[i+1]):
                    j_idx = A_indices[j]
                    incr = (grad_i - mem_i) * A_data[j] + d[j_idx] * (
                        gradient_average[j_idx] + f_alpha * x[j_idx])
                    x[j_idx] = prox(x[j_idx] - step_size * incr, step_size * d[j_idx])

                # .. update memory terms ..
                for j in range(A_indptr[i], A_indptr[i+1]):
                    j_idx = A_indices[j]
                    gradient_average[j_idx] += (grad_i - memory_gradient[i]) * A_data[j] / n_samples
                memory_gradient[i] = grad_i

            if job_id == 0:
                if trace:
                    trace_x[it, :] = x
                # .. convergence check ..
                cert = np.linalg.norm(x - x_old) / step_size
                x_old[:] = x
                if cert < tol:
                    stop_flag[0] = True
            elif job_id == 1:
                # .. recompute alpha bar ..
                grad_tmp = np.zeros(n_features)
                for i in sample_indices:
                    for j in range(A_indptr[i], A_indptr[i + 1]):
                        j_idx = A_indices[j]
                        grad_tmp[j_idx] += memory_gradient[i] * A_data[j] / n_samples
                # .. copy back to shared memory ..
                gradient_average[:] = grad_tmp

            if stop_flag[0]:
                break
        return it, cert

    return epoch_iteration_template


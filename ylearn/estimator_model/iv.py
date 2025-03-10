from copy import deepcopy

import numpy as np

from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import (OneHotEncoder, OrdinalEncoder,
                                   PolynomialFeatures)

from .base_models import BaseEstModel
from .utils import (convert2array, nd_kron, get_wv)


# class TwoSLS(BaseEstModel):
#     # TODO: simply import the 2SLS from StatsModel can finish this
#     def __init__(
#         self,
#         random_state=2022,
#         is_discrete_treatment=False,
#         is_discrete_outcome=False,
#         categories='auto'
#     ):
#         super().__init__(
#             random_state,
#             is_discrete_treatment,
#             is_discrete_outcome,
#             categories
#         )

#     def fit(self, data, outcome, treatment, **kwargs):
#         return super().fit(data, outcome, treatment, **kwargs)

#     def estimate(self, data=None, **kwargs):
#         return super().estimate(data, **kwargs)

#     def _prepare4est(self):
#         pass


class NP2SLS(BaseEstModel):
    r"""
    See Instrumental variable estimation of nonparametric models
    (https://eml.berkeley.edu/~powell/npiv.pdf) for reference.

    This method is similar to the conventional 2SLS and is also composed of
    2 stages after finding new features of x, w, and z,  
        f^d = f^d(z)
        g^\mu = g^\mu(v),
    which are represented by some non-linear functions (basis functions). These
    stages are:
        1. Fit the treatment model:
            x_hat(z, v, w) = a^{d, \mu} f_d(z)g_{\mu}(v) + h(v, w) + \eta
        2. Generate new treatments x_hat, and then fit the outcome model
            y(x_hat, v, w) = a^{m, \mu} \psi_m(x_hat)g_{\mu}(v) + k(v, w) 
            + \epsilon.
    The final causal effect is then estimated as
        y(x_hat_1, v, w) - y(x_hat_0, v, w).
    """

    def __init__(
        self,
        x_model=None,
        y_model=None,
        random_state=2022,
        is_discrete_treatment=False,
        is_discrete_outcome=False,
        categories='auto'
    ):
        self.x_model = LinearRegression() if x_model is None else x_model
        self.y_model = LinearRegression() if y_model is None else y_model

        super().__init__(
            random_state,
            is_discrete_treatment,
            is_discrete_outcome,
            categories
        )

    def fit(
        self,
        data,
        outcome,
        treatment,
        instrument,
        is_discrete_instrument=False,
        treatment_basis=('Poly', 2),
        instrument_basis=('Poly', 2),
        covar_basis=('Poly', 2),
        adjustment=None,
        covariate=None,
        **kwargs
    ):
        """Note that when both treatment_basis and instrument_basis have degree
        1 we are actually doing 2SLS.
        """
        assert instrument is not None, 'Instrument is required.'

        if all(
            (treatment_basis is None, instrument_basis is None,
             covar_basis is None,)
        ):
            raise ValueError(
                'Please specifiy the non-linear transformers for variables, '
                'or use TwoSLS method instead.'
            )

        super().fit(
            data, outcome, treatment,
            covariate=covariate,
            adjustment=adjustment,
            instrument=instrument,
        )
        self.is_discrete_instrument = is_discrete_instrument

        self._n = len(data)

        # data preprocessing
        y, x, z, w, v = convert2array(
            data, outcome, treatment, instrument, adjustment, covariate
        )
        self._y_d = y.shape[1]
        
        if self.is_discrete_treatment:
            self.treatment_transformer = OrdinalEncoder()
            self.treatment_transformer.fit(x)
            x = self.treatment_transformer.transform(x)

        if self.is_discrete_instrument:
            self.instrument_transformer = OneHotEncoder()
            self.instrument_transformer.fit(z)
            z = self.instrument_transformer.transform(z)

        if isinstance(treatment_basis, list) \
           or isinstance(treatment_basis, tuple):
            method, degree = treatment_basis[0], treatment_basis[1]
            self._x_basis_func = self.get_basis_func(
                degree, method, x, **kwargs
            )
        else:
            self._x_basis_func = deepcopy(treatment_basis)

        if isinstance(instrument_basis, list) \
           or isinstance(instrument_basis, tuple):
            method, degree = instrument_basis[0], instrument_basis[1]
            self._z_basis_func = self.get_basis_func(
                degree, method, z, **kwargs
            )
        else:
            self._z_basis_func = deepcopy(instrument_basis)

        if isinstance(covar_basis, str) or isinstance(covar_basis, tuple):
            method, degree = covar_basis[0], covar_basis[1]
            self._v_basis_func = self.get_basis_func(
                degree, method, v, **kwargs
            )
        else:
            self._v_basis_func = deepcopy(covar_basis)

        # get expansion basis functions for parameters
        x_transformed = self._x_basis_func.fit_transform(x)
        z_transformed = self._z_basis_func.fit_transform(z)
        v_transformed = self._v_basis_func.fit_transform(v)

        self._v_transformed = v_transformed
        self._w = w

        if v_transformed is not None:
            zv = nd_kron(z_transformed, v_transformed)
        else:
            zv = z_transformed

        zvw = get_wv(zv, w, np.ones((zv.shape[0], 1)))

        # stage 1: fit the x_model on z and w, v
        self.x_model.fit(zvw, x_transformed)

        # stage 2: fit the y_model on x, and w, v
        x_hat = self.x_model.predict(zvw)

        if v_transformed is not None:
            xv = nd_kron(x_hat, v_transformed)
        else:
            xv = x_hat

        xvw = get_wv(xv, w, np.ones((xv.shape[0], 1)))
        self.y_model.fit(xvw, y)

        # set _is_fitted as True
        self._is_fitted = True

        return self

    def estimate(
        self,
        data=None,
        treat=None,
        control=None,
        quantity=None,
        marginal_effect=False,
    ):
        if not self._is_fitted:
            raise Exception('The estimator is not fitted yet.')

        if hasattr(self, 'treat') and treat is None:
            treat = self.treat
        if hasattr(self, 'control') and control is None:
            control = self.control

        yt, y0 = self._prepare4est(
            data=data,
            treat=treat,
            control=control,
            marginal_effect=marginal_effect,
        )
        if quantity == 'CATE':
            assert self.covariate is not None,\
                'Need caovariate to compute the CATE in this case.'
            return (yt - y0).mean(dim=0)
        if quantity == 'ATE':
            return (yt - y0).mean(dim=0)
        elif quantity == 'ITE':
            return (yt - y0)
        else:
            return yt

    def effect_nji(self, data=None):
        if self.is_discrete_treatment:
            v, w, x, n = self._check_data(data=data)
            x_d = len(self.treatment_transformer.categories_[0])
            y_nji = np.full((n, self._y_d, x_d), np.nan)
            
            for treat in range(x_d):
                xt = np.repeat(np.array([[treat]]), n, axis=0)
                xt = self._x_basis_func.fit_transform(xt)
                
                xv_t = nd_kron(xt, v) if v is not None else xt
                xvw_t = get_wv(xv_t, w, np.ones((n, 1)))
                y_pred = self.y_model.predict(xvw_t).reshape(-1, self._y_d)
                
                y_nji[:, :, treat] = y_pred
            
            y_ctrl = y_nji[:, :, 0].reshape(n, -1, 1).repeat(x_d, aixs=2)
        else:
            yt, y0 = self._prepare4est(data=data, marginal_effect=False)

            if yt.ndim == 1:
                yt = yt.reshape(-1, 1)
                y0 = y0.reshape(-1, 1)
            
            n, y_d = yt.shape
            y_nji = np.full(n, y_d, 2)

            y_nji[:, :, 0] = y0
            y_nji[:, :, 1] = yt
            y_ctrl = y0.reshape(n, -1, 1).repeat(2, axis=2)
        
        y_nji = y_nji - y_ctrl
        
        return y_nji

    def _prepare4est(
        self,
        data,
        treat,
        control,
        marginal_effect,
    ):
        v, w, x, n = self._check_data(data=data)

        if x is None:
            if self.is_discrete_treatment:
                treat = 1 if treat is None \
                    else self.treatment_transformer.transform(treat)
                control = 0 if control is None else \
                    self.treatment_transformer.transform(control)
            else:
                treat = 1 if treat is None else treat
                control = 0 if control is None else control

            xt = np.repeat(np.array([[treat]]), n, axis=0)
            x0 = np.repeat(np.array([[control]]), n, axis=0)
        else:
            xt = x
            x0 = deepcopy(xt)

        xt = self._x_basis_func.fit_transform(xt)
        x0 = self._x_basis_func.fit_transform(x0)

        if v is not None:
            xv_t = nd_kron(xt, v)
            xv_0 = nd_kron(x0, v)
        else:
            xv_t = xt
            xv_0 = x0

        xvw_t = get_wv(xv_t, w, np.ones((n, 1)))
        xvw_0 = get_wv(xv_0, w, np.ones((n, 1)))

        yt = self.y_model.predict(xvw_t)
        y0 = self.y_model.predict(xvw_0)

        return yt, y0

    def _check_data(self, data):
        if data is None:
            v = self._v_transformed
            w = self._w
            x = None
            n = self._n
        else:
            x, w, v = convert2array(
                data, self.treatment, self.adjustment, self.covariate
            )
            v = self._v_basis_func.fit_transform(v) if v is not None else None

            n = len(data)

        return v, w, x, n

    def get_basis_func(self, degree, func, para, **kwargs):
        if func == 'Poly':
            return self._poly_basis_func(degree, para, **kwargs)
        elif func == 'Hermite':
            return self._hermite_basis_func(degree, para, **kwargs)
        else:
            raise ValueError(f'Do not support {func} currently.')

    def _poly_basis_func(self, degree, para, **kwargs):
        poly = PolynomialFeatures(degree=degree, **kwargs)
        return poly

    def _hermite_basis_func(self, degree, para, **kwargs):
        raise NotImplementedError

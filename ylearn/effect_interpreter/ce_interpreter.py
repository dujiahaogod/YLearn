# TODO: In the current implementation, we believe that the tree model can be a
# valid model for predicting the causal effects estimated by the estimator model. Therefore,
# we can directly apply the fitted tree model to new test dataset and add support
# for such method. In the later version, we may need to verify the correctness of
# doing so and modify the method accordingly.

import numpy as np
import pandas as pd

from sklearn.tree import plot_tree
from sklearn.tree import DecisionTreeRegressor

from ylearn.estimator_model.utils import convert2array


class CEInterpreter:
    def __init__(
        self, *,
        criterion='squared_error',
        splitter='best',
        max_depth=None,
        min_samples_split=2,
        min_samples_leaf=1,
        min_weight_fraction_leaf=0.0,
        max_features=None,
        random_state=None,
        max_leaf_nodes=None,
        min_impurity_decrease=0.,
        ccp_alpha=0.0,
    ):
        """
        Many parameters are similar to those of BaseDecisionTree of sklearn.

        Parameters
        ----------
        criterion : {"squared_error", "friedman_mse", "absolute_error", \
                "poisson"}, default="squared_error"
            The function to measure the quality of a split. Supported criteria
            are "squared_error" for the mean squared error, which is equal to
            variance reduction as feature selection criterion and minimizes the L2
            loss using the mean of each terminal node, "friedman_mse", which uses
            mean squared error with Friedman's improvement score for potential
            splits, "absolute_error" for the mean absolute error, which minimizes
            the L1 loss using the median of each terminal node, and "poisson" which
            uses reduction in Poisson deviance to find splits.        

        splitter : {"best", "random"}, default="best"
            The strategy used to choose the split at each node. Supported
            strategies are "best" to choose the best split and "random" to choose
            the best random split.

        max_depth : int, default=None
            The maximum depth of the tree. If None, then nodes are expanded until
            all leaves are pure or until all leaves contain less than
            min_samples_split samples.

        min_samples_split : int or float, default=2
            The minimum number of samples required to split an internal node:
            - If int, then consider `min_samples_split` as the minimum number.
            - If float, then `min_samples_split` is a fraction and
            `ceil(min_samples_split * n_samples)` are the minimum
            number of samples for each split.

        min_samples_leaf : int or float, default=1
            The minimum number of samples required to be at a leaf node.
            A split point at any depth will only be considered if it leaves at
            least ``min_samples_leaf`` training samples in each of the left and
            right branches.  This may have the effect of smoothing the model,
            especially in regression.
            - If int, then consider `min_samples_leaf` as the minimum number.
            - If float, then `min_samples_leaf` is a fraction and
            `ceil(min_samples_leaf * n_samples)` are the minimum
            number of samples for each node.

        min_weight_fraction_leaf : float, default=0.0
            The minimum weighted fraction of the sum total of weights (of all
            the input samples) required to be at a leaf node. Samples have
            equal weight when sample_weight is not provided.

        max_features : int, float or {"sqrt", "log2"}, default=None
            The number of features to consider when looking for the best split:
            - If int, then consider `max_features` features at each split.
            - If float, then `max_features` is a fraction and
            `int(max_features * n_features)` features are considered at each
            split.
            - If "sqrt", then `max_features=sqrt(n_features)`.
            - If "log2", then `max_features=log2(n_features)`.
            - If None, then `max_features=n_features`.

        random_state : int
            Controls the randomness of the estimator.

        max_leaf_nodes : int, default to None
            Grow a tree with ``max_leaf_nodes`` in best-first fashion.
            Best nodes are defined as relative reduction in impurity.
            If None then unlimited number of leaf nodes.

        min_impurity_decrease : float, default=0.0
            A node will be split if this split induces a decrease of the impurity
            greater than or equal to this value.
            The weighted impurity decrease equation is the following::
                N_t / N * (impurity - N_t_R / N_t * right_impurity
                                    - N_t_L / N_t * left_impurity)
            where ``N`` is the total number of samples, ``N_t`` is the number of
            samples at the current node, ``N_t_L`` is the number of samples in the
            left child, and ``N_t_R`` is the number of samples in the right child.
            ``N``, ``N_t``, ``N_t_R`` and ``N_t_L`` all refer to the weighted sum,
            if ``sample_weight`` is passed.

        ccp_alpha : non-negative float, default to 0.0
            Value for pruning the tree. #TODO: not implemented yet.
        """
        self._is_fitted = False
        self.treatment = None
        self.outcome = None

        self._tree_model = DecisionTreeRegressor(
            criterion=criterion,
            splitter=splitter,
            max_depth=max_depth,
            min_samples_leaf=min_samples_leaf,
            min_samples_split=min_samples_split,
            min_weight_fraction_leaf=min_weight_fraction_leaf,
            max_features=max_features,
            random_state=random_state,
            max_leaf_nodes=max_leaf_nodes,
            min_impurity_decrease=min_impurity_decrease,
            ccp_alpha=ccp_alpha
        )

    def fit(
        self,
        data,
        est_model,
        **kwargs
    ):
        """Fit the CEInterpreter model to interpret the causal effect estimated
        by the est_model on data.

        Parameters
        ----------
        data : pandas.DataFrame
            The input samples for the est_model to estimate the causal effects
            and for the CEInterpreter to fit.

        est_model : estimator_model
            est_model should be any valid estimator model of ylearn which was 
            already fitted and can estimate the CATE.
        """
        assert est_model._is_fitted

        self.covariate = est_model.covariate
        assert self.covariate is not None, 'Need covariate to interpret the causal effect.'

        v = convert2array(data, self.covariate)[0]
        n = v.shape[0]
        if hasattr(est_model, 'covariate_transformer'):
            self.cov_transformer = est_model.covariate_transformer
            v = self.cov_transformer.transform(v)

        self._v = v

        causal_effect = est_model.estimate(data=data, quantity=None, **kwargs)

        self._tree_model.fit(v, causal_effect.reshape((n, -1)))

        self._is_fitted = True

        return self

    def interpret(self, *, v=None, data=None):
        assert self._is_fitted, 'The model is not fitted yet. Please use the fit method first.'

        v = self._check_features(v=v, data=data)

        node_indicator = self._tree_model.decision_path(v)
        leaf_id = self._tree_model.apply(v)
        feature = self._tree_model.tree_.feature
        threshold = self._tree_model.tree_.threshold

        result_dict = {}
        
        for sample_id, sample in enumerate(v):
            result_dict[f'sample_{sample_id}'] = ''
            node_index = node_indicator.indices[
                node_indicator.indptr[sample_id]: node_indicator.indptr[sample_id + 1]
            ]

            for node_id in node_index:
                if leaf_id[sample_id] == node_id:
                    continue

                if v[sample_id, feature[node_id]] <= threshold[node_id]:
                    thre_sign = '<='
                else:
                    thre_sign = '>'

                feature_id = feature[node_id]
                value = v[sample_id, feature_id]
                thre = threshold[node_id]
                result_dict[f'sample_{sample_id}'] += f'decision node {node_id}: (covariate [{sample_id}, {feature_id}] = {value})' \
                    f' {thre_sign} {thre} \n'
        
        return result_dict

    def _check_features(self, *, v=None, data=None):
        """Validate the training data on predict (probabilities)."""

        if v is not None:
            v = v.reshape(-1, 1) if v.ndim == 1 else v
            assert v.shape[1] == self._tree_model.n_features_in_
            v = v.astype(np.float32)

            return v

        if data is None:
            v = self._v
        else:
            assert isinstance(data, pd.DataFrame)

            v = convert2array(data, self.covariate)[0]
            if hasattr(self, 'cov_transformer'):
                v = self.cov_transformer.transform(v)

            assert v.shape[1] == self._tree_model.n_features_in_
            v = v.reshape(-1, 1) if v.ndim == 1 else v

        v = v.astype(np.float32)

        return v

    def plot(
        self, *,
        feature_names=None,
        max_depth=None,
        class_names=None,
        label='all',
        filled=False,
        node_ids=False,
        proportion=False,
        rounded=False,
        precision=3,
        ax=None,
        fontsize=None
    ):
        """Plot a policy tree.
        The sample counts that are shown are weighted with any sample_weights that
        might be present.
        The visualization is fit automatically to the size of the axis.
        Use the ``figsize`` or ``dpi`` arguments of ``plt.figure``  to control
        the size of the rendering.

        Parameters
        ----------        
        max_depth : int, default=None
            The maximum depth of the representation. If None, the tree is fully
            generated.
        
        class_names : list of str or bool, default=None
            Names of each of the target classes in ascending numerical order.
            Only relevant for classification and not supported for multi-output.
            If ``True``, shows a symbolic representation of the class name.
        
        label : {'all', 'root', 'none'}, default='all'
            Whether to show informative labels for impurity, etc.
            Options include 'all' to show at every node, 'root' to show only at
            the top root node, or 'none' to not show at any node.
        
        filled : bool, default=False
            When set to ``True``, paint nodes to indicate majority class for
            classification, extremity of values for regression, or purity of node
            for multi-output.
        
        impurity : bool, default=True
            When set to ``True``, show the impurity at each node.
        
        node_ids : bool, default=False
            When set to ``True``, show the ID number on each node.
        
        proportion : bool, default=False
            When set to ``True``, change the display of 'values' and/or 'samples'
            to be proportions and percentages respectively.
        
        rounded : bool, default=False
            When set to ``True``, draw node boxes with rounded corners and use
            Helvetica fonts instead of Times-Roman.
        
        precision : int, default=3
            Number of digits of precision for floating point in the values of
            impurity, threshold and value attributes of each node.
        
        ax : matplotlib axis, default=None
            Axes to plot to. If None, use current axis. Any previous content
            is cleared.
        
        fontsize : int, default=None
            Size of text font. If None, determined automatically to fit figure.
        
        Returns
        -------
        annotations : list of artists
            List containing the artists for the annotation boxes making up the
            tree.
        """
        assert self._is_fitted

        impurity = False

        if feature_names == None:
            feature_names = self.covariate

        return plot_tree(
            self._tree_model,
            max_depth=max_depth,
            feature_names=feature_names,
            class_names=class_names,
            label=label,
            filled=filled,
            impurity=impurity,
            node_ids=node_ids,
            proportion=proportion,
            rounded=rounded,
            precision=precision,
            ax=ax,
            fontsize=fontsize,
        )
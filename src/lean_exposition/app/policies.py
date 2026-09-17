"""Bind production recommendation inputs to each immutable library structure."""
from lean_exposition.features import FEATURE_CONFIG_DIGEST, FeatureSet, extract_features
from lean_exposition.recommendation import make_structural_policy


def library_policy(stores, feature_paths=None):
    feature_paths = feature_paths or {}
    policies = {}
    supplied = {}
    for store in sorted(stores, key=lambda item: feature_paths.get(item.instance_id) is None):
        path = feature_paths.get(store.instance_id)
        if store.structure_id in policies and path is None:
            continue
        features = (FeatureSet.load(path) if path else extract_features(
            store.workspace, store.hierarchy,
            dependency_analysis=store.dependency_analysis,
        ))
        if features.config_digest != FEATURE_CONFIG_DIGEST:
            raise ValueError('Recommendation feature configuration does not match this reader.')
        if features.repo_key != store.hierarchy['repo_key'] or features.workspace_digest != store.workspace.digest():
            raise ValueError('Recommendation features belong to a different fixed input.')
        # The factory verifies the source digest and hierarchy identity.
        policy = make_structural_policy(
            store.workspace, store.hierarchy, features,
            dependency_analysis=store.dependency_analysis,
        )
        if store.structure_id in supplied and supplied[store.structure_id] != features.to_dict():
            raise ValueError('Language packages supplied different features for one structure.')
        policies[store.structure_id] = policy
        supplied[store.structure_id] = features.to_dict()

    def recommend(context, candidates):
        if context['structure_id'] not in policies:
            raise ValueError('No recommendation policy is bound to this structure.')
        return policies[context['structure_id']](context, candidates)
    return recommend

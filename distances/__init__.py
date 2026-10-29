"""Distance tools; nationwide preprocessing does not import the legacy toy pipeline."""

__all__ = ["RoadDistances", "build_distance_cache", "load_distances", "submatrix", "road_km"]


def __getattr__(name):
    if name == "RoadDistances":
        from .lookup import RoadDistances
        return RoadDistances
    if name in __all__:
        from . import compute
        return getattr(compute, name)
    raise AttributeError(name)

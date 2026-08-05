"""Shared test fixtures.

``src.renderer`` imports ``starplot`` at module level. The real library is heavy
(DuckDB + matplotlib + large catalogs) and only needed for an actual render,
which the tests never perform. So if starplot is not installed (e.g. local dev),
we register a lightweight stub in ``sys.modules`` before any ``src`` module is
imported. In CI the real library is installed and this stub is skipped, so the
renderer's pure helpers run against the genuine optic/plot classes.
"""

import sys
import types


def _install_starplot_stub() -> None:
    try:
        import starplot  # noqa: F401  pylint: disable=import-outside-toplevel,unused-import

        return
    except ImportError:
        pass

    starplot = types.ModuleType("starplot")

    class _Recorder:
        """Stand-in for a starplot class: records ctor kwargs, no-ops everything else."""

        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def __getattr__(self, _name):
            return lambda *a, **k: None

    # Optic and plot classes — distinct subclasses so isinstance() is meaningful.
    for _name in (
        "Binoculars",
        "Camera",
        "Scope",
        "Refractor",
        "Reflector",
        "Observer",
        "Miller",
        "MapPlot",
        "GalaxyPlot",
        "HorizonPlot",
        "OpticPlot",
        "ZenithPlot",
    ):
        setattr(starplot, _name, type(_name, (_Recorder,), {}))

    class _ClassAttrRecorder(type):
        """Metaclass so e.g. DSO.get(...) (a *class*-level call, no instance)
        resolves to a no-op returning None, same spirit as _Recorder above."""

        def __getattr__(cls, _name):
            return lambda *a, **k: None

    # Catalog model classes (src.renderer._resolve_object_name): only called as
    # classmethods (DSO.get/.find, Star.find, Planet.get, Sun.get, Moon.get),
    # never instantiated — hence the metaclass instead of _Recorder.
    for _name in ("DSO", "Star", "Planet", "Sun", "Moon"):
        setattr(starplot, _name, _ClassAttrRecorder(_name, (), {}))

    class _Settings:
        data_path = None
        language = None

    starplot.settings = _Settings()

    class _DeferredExprStub:
        """Stand-in for ibis's `_` deferred column expression. Every attribute
        access, call, and comparison returns another instance of itself, so
        chains like `_.name.lower() == x` or `(_.magnitude < 8) | _.magnitude.isnull()`
        build without error. The resulting "expression" is never actually
        evaluated: the catalog classmethods that consume it (DSO.find,
        Star.find, `where=[...]` on p.stars/p.nebula/etc.) are themselves
        no-ops under this stub."""

        def __getattr__(self, _name):
            return self

        def __call__(self, *args, **kwargs):
            return self

        def __eq__(self, other):
            return self

        def __ne__(self, other):
            return self

        def __lt__(self, other):
            return self

        def __le__(self, other):
            return self

        def __gt__(self, other):
            return self

        def __ge__(self, other):
            return self

        def __or__(self, other):
            return self

        def __and__(self, other):
            return self

        def __invert__(self):
            return self

    starplot._ = _DeferredExprStub()  # the ibis column placeholder; only used inside renders

    styles = types.ModuleType("starplot.styles")

    class PlotStyle:
        def extend(self, *args, **kwargs):
            return self

    styles.PlotStyle = PlotStyle
    styles.extensions = types.SimpleNamespace(MAP=object(), OPTIC=object(), BLUE_NIGHT=object())
    starplot.styles = styles

    sys.modules["starplot"] = starplot
    sys.modules["starplot.styles"] = styles


_install_starplot_stub()

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

    class _Settings:
        data_path = None
        language = None

    starplot.settings = _Settings()
    starplot._ = _Recorder()  # the ibis column placeholder; only used inside renders

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

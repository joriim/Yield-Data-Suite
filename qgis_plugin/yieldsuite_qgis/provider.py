# SPDX-License-Identifier: GPL-2.0-or-later
from qgis.core import QgsProcessingProvider

from .algorithms import ALGORITHMS


class YieldSuiteProvider(QgsProcessingProvider):
    def id(self):
        return "yieldsuite"

    def name(self):
        return "Miiro"

    def longName(self):  # noqa: N802
        return "Miiro: open modelling of crop yield and its causes"

    def loadAlgorithms(self):  # noqa: N802
        for alg in ALGORITHMS:
            self.addAlgorithm(alg())

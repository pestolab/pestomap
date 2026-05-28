import os
from qgis.PyQt.QtWidgets import QAction, QMessageBox
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import QSettings
from .pestomap_dialog import PestoMapDialog


class PestoMapPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.dialog = None

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, 'icon.png')
        if not os.path.exists(icon_path):
            icon = QIcon()
        else:
            icon = QIcon(icon_path)

        self.action = QAction(icon, 'PestoMap', self.iface.mainWindow())
        self.action.setToolTip('PestoMap — GIS 자동화')
        self.action.triggered.connect(self.run)

        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu('PestoMap', self.action)

    def unload(self):
        self.iface.removePluginMenu('PestoMap', self.action)
        self.iface.removeToolBarIcon(self.action)
        if self.dialog:
            self.dialog.close()

    def run(self):
        self.dialog = PestoMapDialog(self.iface, self.iface.mainWindow())
        self.dialog.show()
        self.dialog.exec()

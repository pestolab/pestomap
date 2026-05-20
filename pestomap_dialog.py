import os
import json
import glob

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QLineEdit, QFileDialog,
    QProgressBar, QComboBox, QGroupBox, QCheckBox,
    QMessageBox, QTextEdit, QSizePolicy
)
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal
from qgis.PyQt.QtGui import QFont, QColor

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsRasterLayer,
    QgsSymbol, QgsSimpleFillSymbolLayer, QgsSimpleLineSymbolLayer,
    QgsSingleSymbolRenderer, QgsWkbTypes,
    QgsPrintLayout, QgsLayoutItemMap, QgsLayoutItemScaleBar,
    QgsLayoutItemPicture, QgsLayoutItemLabel,
    QgsLayoutPoint, QgsLayoutSize, QgsUnitTypes,
    QgsLayoutExporter, QgsApplication, QgsRectangle
)


PLUGIN_DIR = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(PLUGIN_DIR, 'config.json')


def load_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def match_layer_config(layer_name, config):
    """레이어 이름에서 키워드를 찾아 색상 설정 반환"""
    name_lower = layer_name.lower()
    for keyword, preset in config['layers'].items():
        if keyword in layer_name:
            return preset
    return config['default']


def apply_style(layer, preset):
    """레이어에 색상 스타일 적용"""
    geom_type = layer.geometryType()

    if geom_type in (QgsWkbTypes.PolygonGeometry,):
        symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.PolygonGeometry)
        sym_layer = QgsSimpleFillSymbolLayer()
        fill = QColor(preset['fill_color'])
        fill.setAlphaF(preset.get('opacity', 0.8))
        sym_layer.setFillColor(fill)
        sym_layer.setStrokeColor(QColor(preset['stroke_color']))
        sym_layer.setStrokeWidth(preset.get('stroke_width', 0.26))
        symbol.changeSymbolLayer(0, sym_layer)

    elif geom_type in (QgsWkbTypes.LineGeometry,):
        symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.LineGeometry)
        sym_layer = QgsSimpleLineSymbolLayer()
        stroke = QColor(preset['stroke_color'])
        stroke.setAlphaF(preset.get('opacity', 1.0))
        sym_layer.setColor(stroke)
        sym_layer.setWidth(preset.get('stroke_width', 0.5))
        symbol.changeSymbolLayer(0, sym_layer)

    elif geom_type in (QgsWkbTypes.PointGeometry,):
        symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.PointGeometry)
        fill = QColor(preset.get('fill_color', '#F44336'))
        symbol.setColor(fill)

    else:
        return

    renderer = QgsSingleSymbolRenderer(symbol)
    layer.setRenderer(renderer)
    layer.triggerRepaint()


def create_print_layout(iface, output_path, fmt, config):
    """스케일바·방위표 포함 인쇄 레이아웃 생성 후 출력"""
    project = QgsProject.instance()
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()

    page = layout.pageCollection().pages()[0]
    paper = config.get('output', {})
    dpi = paper.get('dpi', 300)
    orientation = paper.get('orientation', 'landscape')

    if paper.get('paper_size', 'A3') == 'A3':
        if orientation == 'landscape':
            page.setPageSize(QgsLayoutSize(420, 297, QgsUnitTypes.LayoutMillimeters))
        else:
            page.setPageSize(QgsLayoutSize(297, 420, QgsUnitTypes.LayoutMillimeters))
    else:
        if orientation == 'landscape':
            page.setPageSize(QgsLayoutSize(297, 210, QgsUnitTypes.LayoutMillimeters))
        else:
            page.setPageSize(QgsLayoutSize(210, 297, QgsUnitTypes.LayoutMillimeters))

    margin = paper.get('margin_mm', 10)
    w = page.pageSize().width()
    h = page.pageSize().height()

    # 지도 아이템
    map_item = QgsLayoutItemMap(layout)
    layout.addLayoutItem(map_item)
    map_h = h - margin * 2 - 20   # 하단 스케일바 공간 확보
    map_w = w - margin * 2 - 35   # 우측 방위표 공간 확보
    map_item.attemptMove(
        QgsLayoutPoint(margin, margin, QgsUnitTypes.LayoutMillimeters)
    )
    map_item.attemptResize(
        QgsLayoutSize(map_w, map_h, QgsUnitTypes.LayoutMillimeters)
    )
    map_item.zoomToExtent(iface.mapCanvas().extent())
    map_item.setMapRotation(0)

    # 스케일바
    scale_bar = QgsLayoutItemScaleBar(layout)
    layout.addLayoutItem(scale_bar)
    scale_bar.setLinkedMap(map_item)
    scale_bar.applyDefaultSize()
    scale_bar.setStyle('Single Box')
    scale_bar.setUnits(QgsUnitTypes.DistanceMeters)
    scale_bar.setNumberOfSegments(4)
    scale_bar.setNumberOfSegmentsLeft(0)
    scale_bar.setFont(QFont('Arial', 7))
    scale_bar.attemptMove(
        QgsLayoutPoint(margin, h - margin - 12, QgsUnitTypes.LayoutMillimeters)
    )
    scale_bar.attemptResize(
        QgsLayoutSize(60, 10, QgsUnitTypes.LayoutMillimeters)
    )

    # 방위표 (QGIS 내장 SVG)
    north_arrow = QgsLayoutItemPicture(layout)
    layout.addLayoutItem(north_arrow)
    prefix = QgsApplication.prefixPath()
    svg_candidates = [
        os.path.join(prefix, 'svg', 'arrows', 'NorthArrow_02.svg'),
        os.path.join(prefix, 'svg', 'arrows', 'NorthArrow_01.svg'),
        os.path.join(prefix, 'svg', 'north_arrows', 'NorthArrow_02.svg'),
        # Windows QGIS 일반 경로
        r'C:\Program Files\QGIS 3.34\apps\qgis\svg\arrows\NorthArrow_02.svg',
        r'C:\Program Files\QGIS 3.28\apps\qgis\svg\arrows\NorthArrow_02.svg',
    ]
    for svg in svg_candidates:
        if os.path.exists(svg):
            north_arrow.setPicturePath(svg)
            break
    north_arrow.attemptMove(
        QgsLayoutPoint(w - margin - 28, margin, QgsUnitTypes.LayoutMillimeters)
    )
    north_arrow.attemptResize(
        QgsLayoutSize(22, 28, QgsUnitTypes.LayoutMillimeters)
    )

    # 출력
    exporter = QgsLayoutExporter(layout)
    if fmt == 'pdf':
        pdf_settings = QgsLayoutExporter.PdfExportSettings()
        pdf_settings.dpi = dpi
        result = exporter.exportToPdf(output_path, pdf_settings)
    else:
        img_settings = QgsLayoutExporter.ImageExportSettings()
        img_settings.dpi = dpi
        result = exporter.exportToImage(output_path, img_settings)

    return result == QgsLayoutExporter.Success


class PestoMapDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.config = load_config()
        self.setWindowTitle('PestoMap v0.1')
        self.setMinimumWidth(480)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # 타이틀
        title = QLabel('PestoMap')
        title.setFont(QFont('Arial', 14, QFont.Bold))
        layout.addWidget(title)

        sub = QLabel('shp 폴더 지정 → 레이어 로드 + 색상 적용 + 지도 출력')
        sub.setStyleSheet('color: #666;')
        layout.addWidget(sub)

        # shp 폴더 선택
        shp_group = QGroupBox('1. shp 파일 폴더')
        shp_layout = QHBoxLayout(shp_group)
        self.shp_path = QLineEdit()
        self.shp_path.setPlaceholderText('예: C:\\GIS\\서울\\shp')
        shp_btn = QPushButton('폴더 선택')
        shp_btn.clicked.connect(self._browse_shp)
        shp_layout.addWidget(self.shp_path)
        shp_layout.addWidget(shp_btn)
        layout.addWidget(shp_group)

        # 출력 설정
        out_group = QGroupBox('2. 출력 설정')
        out_layout = QGridLayout(out_group)

        out_layout.addWidget(QLabel('저장 폴더:'), 0, 0)
        self.out_path = QLineEdit()
        self.out_path.setPlaceholderText('비워두면 shp 폴더와 동일 위치에 저장')
        out_path_btn = QPushButton('폴더 선택')
        out_path_btn.clicked.connect(self._browse_output)
        out_layout.addWidget(self.out_path, 0, 1)
        out_layout.addWidget(out_path_btn, 0, 2)

        out_layout.addWidget(QLabel('파일명:'), 1, 0)
        self.out_name = QLineEdit('pestomap_output')
        out_layout.addWidget(self.out_name, 1, 1, 1, 2)

        out_layout.addWidget(QLabel('형식:'), 2, 0)
        self.fmt_combo = QComboBox()
        self.fmt_combo.addItems(['PDF', 'PNG'])
        out_layout.addWidget(self.fmt_combo, 2, 1)

        layout.addWidget(out_group)

        # 옵션
        opt_group = QGroupBox('3. 옵션')
        opt_layout = QVBoxLayout(opt_group)
        self.chk_style = QCheckBox('색상 프리셋 자동 적용')
        self.chk_style.setChecked(True)
        self.chk_scalebar = QCheckBox('스케일바 + 방위표 포함')
        self.chk_scalebar.setChecked(True)
        self.chk_export = QCheckBox('지도 파일로 출력')
        self.chk_export.setChecked(True)
        opt_layout.addWidget(self.chk_style)
        opt_layout.addWidget(self.chk_scalebar)
        opt_layout.addWidget(self.chk_export)
        layout.addWidget(opt_group)

        # 로그
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        self.log.setStyleSheet('font-size: 11px; background: #f8f8f8;')
        layout.addWidget(self.log)

        # 버튼
        btn_layout = QHBoxLayout()
        self.run_btn = QPushButton('실행')
        self.run_btn.setStyleSheet(
            'QPushButton { background: #2B5EA7; color: white; padding: 8px 24px; border-radius: 4px; font-weight: bold; }'
            'QPushButton:hover { background: #1a4a8a; }'
        )
        self.run_btn.clicked.connect(self._run)
        close_btn = QPushButton('닫기')
        close_btn.clicked.connect(self.close)
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        btn_layout.addWidget(self.run_btn)
        layout.addLayout(btn_layout)

    def _browse_shp(self):
        path = QFileDialog.getExistingDirectory(self, 'shp 파일 폴더 선택')
        if path:
            self.shp_path.setText(path)

    def _browse_output(self):
        path = QFileDialog.getExistingDirectory(self, '출력 폴더 선택')
        if path:
            self.out_path.setText(path)

    def _log(self, msg):
        self.log.append(msg)

    def _run(self):
        shp_dir = self.shp_path.text().strip()
        if not shp_dir or not os.path.isdir(shp_dir):
            QMessageBox.warning(self, '오류', 'shp 파일 폴더를 선택해 주세요.')
            return

        self.run_btn.setEnabled(False)
        self.log.clear()
        self._log('시작합니다...')

        # shp 파일 검색 (하위 폴더 포함)
        shp_files = glob.glob(os.path.join(shp_dir, '**', '*.shp'), recursive=True)
        if not shp_files:
            self._log('shp 파일을 찾지 못했습니다.')
            self.run_btn.setEnabled(True)
            return

        self._log(f'shp 파일 {len(shp_files)}개 발견')

        loaded_layers = []
        for shp in shp_files:
            layer_name = os.path.splitext(os.path.basename(shp))[0]
            layer = QgsVectorLayer(shp, layer_name, 'ogr')
            if not layer.isValid():
                self._log(f'  [건너뜀] {layer_name} — 레이어 로드 실패')
                continue

            # 색상 적용
            if self.chk_style.isChecked():
                preset = match_layer_config(layer_name, self.config)
                apply_style(layer, preset)
                self._log(f'  [로드] {layer_name} — {preset.get("label", "기타")} 색상 적용')
            else:
                self._log(f'  [로드] {layer_name}')

            QgsProject.instance().addMapLayer(layer)
            loaded_layers.append(layer)

        self._log(f'{len(loaded_layers)}개 레이어 로드 완료')

        # 출력
        if self.chk_export.isChecked() and loaded_layers:
            out_dir = self.out_path.text().strip() or shp_dir
            out_name = self.out_name.text().strip() or 'pestomap_output'
            fmt = self.fmt_combo.currentText().lower()
            ext = '.pdf' if fmt == 'pdf' else '.png'
            output_path = os.path.join(out_dir, out_name + ext)

            self._log('지도 출력 중...')
            try:
                success = create_print_layout(
                    self.iface, output_path, fmt, self.config
                )
                if success:
                    self._log(f'출력 완료: {output_path}')
                else:
                    self._log('출력 실패 — 레이아웃 생성 중 오류 발생')
            except Exception as e:
                self._log(f'출력 오류: {str(e)}')

        self._log('완료!')
        self.run_btn.setEnabled(True)

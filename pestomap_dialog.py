import os
import json
import glob
import subprocess
import urllib.request
from datetime import datetime

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QLineEdit, QFileDialog,
    QComboBox, QGroupBox, QCheckBox,
    QMessageBox, QTextEdit
)
from qgis.PyQt.QtCore import QThread, pyqtSignal, QObject, pyqtSlot
from qgis.PyQt.QtGui import QFont, QColor, QTextCursor

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeatureRequest,
    QgsSymbol, QgsSimpleFillSymbolLayer, QgsSimpleLineSymbolLayer,
    QgsSingleSymbolRenderer, QgsWkbTypes,
    QgsGraduatedSymbolRenderer, QgsCategorizedSymbolRenderer,
    QgsRendererCategory,
    QgsGradientColorRamp,
    QgsPrintLayout, QgsLayoutItemMap, QgsLayoutItemScaleBar,
    QgsLayoutItemPicture,
    QgsLayoutPoint, QgsLayoutSize,
    QgsLayoutExporter, QgsApplication, QgsRectangle
)

# QGIS 4 분류 메서드
try:
    from qgis.core import (
        QgsClassificationQuantile,
        QgsClassificationEqualInterval,
        QgsClassificationJenks
    )
    HAS_CLASSIFICATION = True
except ImportError:
    HAS_CLASSIFICATION = False

# QGIS 3/4 단위 호환
try:
    from qgis.core import Qgis
    LAYOUT_MM = Qgis.LayoutUnit.Millimeters
    DISTANCE_M = Qgis.DistanceUnit.Meters
except AttributeError:
    from qgis.core import QgsUnitTypes
    LAYOUT_MM = QgsUnitTypes.LayoutMillimeters
    DISTANCE_M = QgsUnitTypes.DistanceMeters

# PyQt5/6 호환
try:
    FONT_BOLD = QFont.Weight.Bold
except AttributeError:
    FONT_BOLD = QFont.Bold


PLUGIN_DIR = os.path.dirname(__file__)
CONFIG_PATH = os.path.join(PLUGIN_DIR, 'config.json')


def load_config():
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        raise RuntimeError(f'config.json을 찾을 수 없습니다: {CONFIG_PATH}')
    except json.JSONDecodeError as e:
        raise RuntimeError(f'config.json 파싱 오류: {e}')


GEMINI_URL = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent'

SYSTEM_PROMPT = """너는 QGIS GIS 데이터 시각화 전문가야. 도시계획 실무자를 돕는 QGIS 플러그인 안에서 동작해.

사용자가 레이어 정보를 주면 분석하고, 자연어 요청을 받아 GIS 시각화 작업을 수행해.
한국 도시계획 데이터(연속지적도, 용도지역, 건축물, 공시지가 등)에 익숙해야 해.

반드시 아래 JSON 형식으로만 응답해 (코드블록, 설명 텍스트 없이 순수 JSON만):
{
  "message": "사용자에게 보여줄 한국어 메시지 (간결하게)",
  "actions": [
    {"type": "graduated", "layer": "레이어명", "field": "필드명", "color_start": "#hex", "color_end": "#hex", "mode": "quantile"},
    {"type": "categorized", "layer": "레이어명", "field": "필드명", "categories": {"값": "#hex"}},
    {"type": "single", "layer": "레이어명", "fill_color": "#hex", "stroke_color": "#hex", "opacity": 0.8},
    {"type": "export", "format": "pdf"}
  ]
}

- actions 없으면 빈 배열 []
- graduated mode: quantile / equal_interval / natural_breaks
- 레이어명은 정확히 일치시킬 것
- 숫자 필드 → graduated, 문자 필드 → categorized 추천"""


# ── Gemini 워커 (QThread 기반, UI 스레드 안전) ──────────────────────────────

class GeminiWorker(QObject):
    finished = pyqtSignal(object, object)  # (parsed_dict, error_str)

    def __init__(self, api_key, prompt):
        super().__init__()
        self.api_key = api_key
        self.prompt = prompt

    @pyqtSlot()
    def run(self):
        try:
            url = f'{GEMINI_URL}?key={self.api_key}'
            body = json.dumps({
                'contents': [{'parts': [{'text': self.prompt}]}],
                'generationConfig': {'temperature': 0.2}
            }).encode('utf-8')
            req = urllib.request.Request(
                url, data=body,
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = json.loads(resp.read().decode('utf-8'))
                text = raw['candidates'][0]['content']['parts'][0]['text'].strip()
                # 코드블록 제거
                if '```' in text:
                    for part in text.split('```'):
                        part = part.strip()
                        if part.startswith('json'):
                            text = part[4:].strip()
                            break
                        if part.startswith('{'):
                            text = part
                            break
                parsed = json.loads(text)
                self.finished.emit(parsed, None)
        except Exception as e:
            self.finished.emit(None, str(e))


# ── 스타일 함수 ──────────────────────────────────────────────────────────────

def match_layer_config(layer_name, config):
    for keyword in config['layers']:
        if keyword in layer_name:
            return config['layers'][keyword]
    return config['default']


def apply_single_style(layer, preset):
    geom_type = int(layer.geometryType())
    if geom_type == 2:
        symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.PolygonGeometry)
        sl = QgsSimpleFillSymbolLayer()
        fill = QColor(preset['fill_color'])
        fill.setAlphaF(preset.get('opacity', 0.8))
        sl.setFillColor(fill)
        sl.setStrokeColor(QColor(preset['stroke_color']))
        sl.setStrokeWidth(preset.get('stroke_width', 0.26))
        symbol.changeSymbolLayer(0, sl)
    elif geom_type == 1:
        symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.LineGeometry)
        sl = QgsSimpleLineSymbolLayer()
        c = QColor(preset['stroke_color'])
        c.setAlphaF(preset.get('opacity', 1.0))
        sl.setColor(c)
        sl.setWidth(preset.get('stroke_width', 0.5))
        symbol.changeSymbolLayer(0, sl)
    elif geom_type == 0:
        symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.PointGeometry)
        symbol.setColor(QColor(preset.get('fill_color', '#F44336')))
    else:
        return
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layer.triggerRepaint()


def apply_graduated_style(layer, field, color_start, color_end, mode='quantile', num_classes=5):
    try:
        color_ramp = QgsGradientColorRamp(QColor(color_start), QColor(color_end))
        renderer = QgsGraduatedSymbolRenderer(field)
        if HAS_CLASSIFICATION:
            method_map = {
                'quantile': QgsClassificationQuantile,
                'natural_breaks': QgsClassificationJenks,
                'equal_interval': QgsClassificationEqualInterval,
            }
            renderer.setClassificationMethod(method_map.get(mode, QgsClassificationQuantile)())
        renderer.updateColorRamp(color_ramp)
        renderer.updateClasses(layer, num_classes)
        layer.setRenderer(renderer)
        layer.triggerRepaint()
        return True
    except Exception:
        return False


def apply_categorized_style(layer, field, categories):
    try:
        geom_map = {0: QgsWkbTypes.PointGeometry, 1: QgsWkbTypes.LineGeometry, 2: QgsWkbTypes.PolygonGeometry}
        qgs_geom = geom_map.get(int(layer.geometryType()), QgsWkbTypes.PolygonGeometry)
        cats = []
        for val, color in categories.items():
            sym = QgsSymbol.defaultSymbol(qgs_geom)
            sym.setColor(QColor(color))
            cats.append(QgsRendererCategory(val, sym, str(val)))
        layer.setRenderer(QgsCategorizedSymbolRenderer(field, cats))
        layer.triggerRepaint()
        return True
    except Exception:
        return False


def build_layer_context(layers):
    lines = []
    for layer in layers:
        geom_label = {0: '포인트', 1: '라인', 2: '폴리곤'}.get(int(layer.geometryType()), '?')
        # 샘플 3개만 먼저 수집 (한 번만 순회)
        sample_feats = list(layer.getFeatures(QgsFeatureRequest().setLimit(3)))
        fields = []
        for f in layer.fields():
            samples = [str(feat[f.name()]) for feat in sample_feats
                       if feat[f.name()] is not None]
            fields.append(f"{f.name()}({f.typeName()}, 예:{','.join(samples)})")
        lines.append(f"레이어: {layer.name()} [{geom_label}]\n  필드: {', '.join(fields[:12])}")
    return '\n'.join(lines)


# ── Print Layout ─────────────────────────────────────────────────────────────

def create_print_layout(iface, output_path, fmt, config, loaded_layers=None):
    project = QgsProject.instance()
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()

    page = layout.pageCollection().pages()[0]
    paper = config.get('output', {})
    dpi = paper.get('dpi', 300)
    orientation = paper.get('orientation', 'landscape')

    if paper.get('paper_size', 'A3') == 'A3':
        page.setPageSize(QgsLayoutSize(420, 297, LAYOUT_MM) if orientation == 'landscape'
                         else QgsLayoutSize(297, 420, LAYOUT_MM))
    else:
        page.setPageSize(QgsLayoutSize(297, 210, LAYOUT_MM) if orientation == 'landscape'
                         else QgsLayoutSize(210, 297, LAYOUT_MM))

    margin = paper.get('margin_mm', 10)
    w = page.pageSize().width()
    h = page.pageSize().height()

    map_item = QgsLayoutItemMap(layout)
    layout.addLayoutItem(map_item)
    map_item.attemptMove(QgsLayoutPoint(margin, margin, LAYOUT_MM))
    map_item.attemptResize(QgsLayoutSize(w - margin * 2 - 35, h - margin * 2 - 20, LAYOUT_MM))

    if loaded_layers:
        combined = QgsRectangle()
        for lyr in loaded_layers:
            combined.combineExtentWith(lyr.extent())
        map_item.zoomToExtent(combined if not combined.isEmpty() else iface.mapCanvas().extent())
    else:
        map_item.zoomToExtent(iface.mapCanvas().extent())
    map_item.setMapRotation(0)

    scale_bar = QgsLayoutItemScaleBar(layout)
    layout.addLayoutItem(scale_bar)
    scale_bar.setLinkedMap(map_item)
    scale_bar.applyDefaultSize()
    scale_bar.setStyle('Single Box')
    scale_bar.setUnits(DISTANCE_M)
    scale_bar.setNumberOfSegments(4)
    scale_bar.setNumberOfSegmentsLeft(0)
    scale_bar.setFont(QFont('Arial', 7))
    scale_bar.attemptMove(QgsLayoutPoint(margin, h - margin - 12, LAYOUT_MM))
    scale_bar.attemptResize(QgsLayoutSize(60, 10, LAYOUT_MM))

    north_arrow = QgsLayoutItemPicture(layout)
    layout.addLayoutItem(north_arrow)
    prefix = QgsApplication.prefixPath()
    for svg in [
        os.path.join(prefix, 'svg', 'arrows', 'NorthArrow_02.svg'),
        os.path.join(prefix, 'svg', 'arrows', 'NorthArrow_01.svg'),
        os.path.join(prefix, 'svg', 'north_arrows', 'NorthArrow_02.svg'),
        r'C:\Program Files\QGIS 3.34\apps\qgis\svg\arrows\NorthArrow_02.svg',
        r'C:\Program Files\QGIS 3.28\apps\qgis\svg\arrows\NorthArrow_02.svg',
    ]:
        if os.path.exists(svg):
            north_arrow.setPicturePath(svg)
            break
    north_arrow.attemptMove(QgsLayoutPoint(w - margin - 28, margin, LAYOUT_MM))
    north_arrow.attemptResize(QgsLayoutSize(22, 28, LAYOUT_MM))

    exporter = QgsLayoutExporter(layout)
    if fmt == 'pdf':
        s = QgsLayoutExporter.PdfExportSettings()
        s.dpi = dpi
        result = exporter.exportToPdf(output_path, s)
    else:
        s = QgsLayoutExporter.ImageExportSettings()
        s.dpi = dpi
        result = exporter.exportToImage(output_path, s)

    return result == QgsLayoutExporter.Success


# ── Dialog ───────────────────────────────────────────────────────────────────

class PestoMapDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.config = load_config()
        self.loaded_layers = []
        self._gemini_thread = None
        self._undo_stack = []       # [(layer, renderer_clone), ...]
        self._session = None        # 현재 세션 dict
        self._session_path = None   # session.json 저장 경로
        self.setWindowTitle('PestoMap v0.3')
        self.setMinimumWidth(520)
        self.setMinimumHeight(640)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel('PestoMap')
        title.setFont(QFont('Arial', 14, FONT_BOLD))
        layout.addWidget(title)

        sub = QLabel('shp 로드 → AI 자동 분석 → 채팅으로 수정')
        sub.setStyleSheet('color: #666;')
        layout.addWidget(sub)

        # 1. shp 폴더
        shp_group = QGroupBox('1. shp 파일 폴더')
        shp_layout = QHBoxLayout(shp_group)
        self.shp_path = QLineEdit()
        self.shp_path.setPlaceholderText('예: C:\\GIS\\서울\\shp')
        shp_btn = QPushButton('폴더 선택')
        shp_btn.clicked.connect(self._browse_shp)
        shp_layout.addWidget(self.shp_path)
        shp_layout.addWidget(shp_btn)
        layout.addWidget(shp_group)

        # 2. 출력 설정
        out_group = QGroupBox('2. 출력 설정')
        out_layout = QGridLayout(out_group)
        out_layout.addWidget(QLabel('저장 폴더:'), 0, 0)
        self.out_path = QLineEdit()
        self.out_path.setPlaceholderText('비워두면 shp 폴더와 동일')
        out_btn = QPushButton('선택')
        out_btn.clicked.connect(self._browse_output)
        out_layout.addWidget(self.out_path, 0, 1)
        out_layout.addWidget(out_btn, 0, 2)
        out_layout.addWidget(QLabel('파일명:'), 1, 0)
        self.out_name = QLineEdit('pestomap_output')
        out_layout.addWidget(self.out_name, 1, 1, 1, 2)
        out_layout.addWidget(QLabel('형식:'), 2, 0)
        self.fmt_combo = QComboBox()
        self.fmt_combo.addItems(['PDF', 'PNG'])
        out_layout.addWidget(self.fmt_combo, 2, 1)
        layout.addWidget(out_group)

        # 3. 옵션
        opt_group = QGroupBox('3. 옵션')
        opt_layout = QHBoxLayout(opt_group)
        self.chk_style = QCheckBox('기본 색상 적용')
        self.chk_style.setChecked(True)
        self.chk_export = QCheckBox('지도 출력')
        self.chk_export.setChecked(True)
        open_cfg_btn = QPushButton('색상 설정 파일 열기')
        open_cfg_btn.clicked.connect(self._open_config)
        opt_layout.addWidget(self.chk_style)
        opt_layout.addWidget(self.chk_export)
        opt_layout.addStretch()
        opt_layout.addWidget(open_cfg_btn)
        layout.addWidget(opt_group)

        # 4. AI 채팅
        ai_group = QGroupBox('4. AI 어시스턴트')
        ai_layout = QVBoxLayout(ai_group)

        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setMinimumHeight(220)
        self.chat_display.setStyleSheet(
            'background:#f8f9fa; font-size:12px; border:1px solid #dee2e6; border-radius:4px; padding:6px;'
        )
        ai_layout.addWidget(self.chat_display)

        input_row = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText('예: 건물 연도별로 색칠해줘 / 공시지가 높은 곳 진하게 / PDF 뽑아줘')
        self.chat_input.returnPressed.connect(self._send_chat)
        self.send_btn = QPushButton('전송')
        self.send_btn.setFixedWidth(60)
        self.send_btn.clicked.connect(self._send_chat)
        input_row.addWidget(self.chat_input)
        input_row.addWidget(self.send_btn)
        ai_layout.addLayout(input_row)
        layout.addWidget(ai_group)

        # 버튼
        btn_row = QHBoxLayout()
        self.run_btn = QPushButton('레이어 로드')
        self.run_btn.setStyleSheet(
            'QPushButton{background:#2B5EA7;color:white;padding:8px 24px;border-radius:4px;font-weight:bold;}'
            'QPushButton:hover{background:#1a4a8a;}'
        )
        self.run_btn.clicked.connect(self._run)
        self.undo_btn = QPushButton('↩ 되돌리기')
        self.undo_btn.setEnabled(False)
        self.undo_btn.setStyleSheet(
            'QPushButton{padding:8px 16px;border-radius:4px;border:1px solid #ccc;}'
            'QPushButton:enabled{color:#333;}'
            'QPushButton:disabled{color:#aaa;}'
        )
        self.undo_btn.clicked.connect(self._undo)
        close_btn = QPushButton('닫기')
        close_btn.clicked.connect(self.close)
        btn_row.addStretch()
        btn_row.addWidget(self.undo_btn)
        btn_row.addWidget(close_btn)
        btn_row.addWidget(self.run_btn)
        layout.addLayout(btn_row)

    # ── UI 헬퍼 ──

    def _browse_shp(self):
        p = QFileDialog.getExistingDirectory(self, 'shp 파일 폴더 선택')
        if p:
            self.shp_path.setText(p)

    def _browse_output(self):
        p = QFileDialog.getExistingDirectory(self, '출력 폴더 선택')
        if p:
            self.out_path.setText(p)

    def _open_config(self):
        try:
            subprocess.Popen(['notepad.exe', CONFIG_PATH])  # Windows
        except Exception:
            try:
                subprocess.Popen(['open', CONFIG_PATH])     # macOS
            except Exception:
                pass

    def _chat(self, speaker, msg):
        colors = {'AI': ('#1a4a8a', '🤖 AI'), 'USER': ('#2d6a2d', '👤 나'), 'SYS': ('#888', '▸')}
        color, label = colors.get(speaker, ('#888', '▸'))
        self.chat_display.append(
            f'<span style="color:{color};font-weight:bold;">{label}</span>&nbsp;{msg}'
        )
        self.chat_display.moveCursor(QTextCursor.MoveOperation.End
                                      if hasattr(QTextCursor, 'MoveOperation')
                                      else QTextCursor.End)

    # ── 레이어 로드 ──

    def _run(self):
        shp_dir = self.shp_path.text().strip()
        if not shp_dir or not os.path.isdir(shp_dir):
            QMessageBox.warning(self, '오류', 'shp 파일 폴더를 선택해 주세요.')
            return

        self.run_btn.setEnabled(False)
        self.chat_display.clear()
        self._undo_stack.clear()
        self.undo_btn.setEnabled(False)
        self._init_session(shp_dir)
        self._chat('SYS', '레이어 로드 중...')

        shp_files = glob.glob(os.path.join(shp_dir, '**', '*.shp'), recursive=True)
        if not shp_files:
            self._chat('SYS', 'shp 파일을 찾지 못했습니다.')
            self.run_btn.setEnabled(True)
            return

        self.loaded_layers = []
        for shp in shp_files:
            name = os.path.splitext(os.path.basename(shp))[0]
            layer = QgsVectorLayer(shp, name, 'ogr')
            if not layer.isValid():
                self._chat('SYS', f'[건너뜀] {name}')
                continue
            geom_label = {0: '포인트', 1: '라인', 2: '폴리곤'}.get(int(layer.geometryType()), '?')
            if self.chk_style.isChecked():
                preset = match_layer_config(name, self.config)
                apply_single_style(layer, preset)
            QgsProject.instance().addMapLayer(layer)
            self.loaded_layers.append(layer)
            self._chat('SYS', f'[로드] {name} [{geom_label}]')

        self._chat('SYS', f'{len(self.loaded_layers)}개 레이어 로드 완료')
        if self._session is not None:
            self._session['layers'] = [l.name() for l in self.loaded_layers]
            self._log_session('load', {'count': len(self.loaded_layers)})

        if self.chk_export.isChecked() and self.loaded_layers:
            out_dir = self.out_path.text().strip() or shp_dir
            out_name = self.out_name.text().strip() or 'pestomap_output'
            fmt = self.fmt_combo.currentText().lower()
            output_path = os.path.join(out_dir, out_name + ('.pdf' if fmt == 'pdf' else '.png'))
            try:
                ok = create_print_layout(self.iface, output_path, fmt, self.config, self.loaded_layers)
                self._chat('SYS', f'출력 완료: {output_path}' if ok else '출력 실패')
            except Exception as e:
                self._chat('SYS', f'출력 오류: {e}')

        self.run_btn.setEnabled(True)

        # AI 자동 분석
        api_key = self.config.get('gemini_api_key', '')
        if api_key and self.loaded_layers:
            ctx = build_layer_context(self.loaded_layers)
            prompt = (f"{SYSTEM_PROMPT}\n\n로드된 레이어:\n{ctx}\n\n"
                      "레이어들을 분석해서 도시계획 실무에 적합한 시각화를 자동 적용해줘. "
                      "어떤 필드 기준으로 무슨 색상을 적용했는지 간결하게 설명해줘.")
            self._chat('AI', '레이어 분석 중...')
            self._call_gemini(prompt)
        else:
            self._chat('AI', '레이어 로드 완료! 아래 입력창에 원하는 작업을 말해주세요.')

    # ── 채팅 전송 ──

    def _send_chat(self):
        msg = self.chat_input.text().strip()
        if not msg:
            return

        # 되돌리기 키워드 감지
        if any(kw in msg for kw in ['되돌려', '취소', 'undo', '원래대로']):
            self._chat('USER', msg)
            self.chat_input.clear()
            self._undo()
            return

        api_key = self.config.get('gemini_api_key', '')
        if not api_key:
            self._chat('SYS', 'config.json에 gemini_api_key가 없습니다.')
            return
        self._chat('USER', msg)
        self._log_session('chat', {'role': 'user', 'text': msg})
        self.chat_input.clear()
        ctx = build_layer_context(self.loaded_layers) if self.loaded_layers else '(로드된 레이어 없음)'
        prompt = f"{SYSTEM_PROMPT}\n\n현재 레이어:\n{ctx}\n\n사용자 요청: {msg}"
        self._call_gemini(prompt)

    # ── Gemini 호출 ──

    def _call_gemini(self, prompt):
        # 이전 스레드 실행 중이면 무시
        if self._gemini_thread is not None and self._gemini_thread.isRunning():
            return

        api_key = self.config.get('gemini_api_key', '')
        self.send_btn.setEnabled(False)

        self._gemini_thread = QThread()
        self._worker = GeminiWorker(api_key, prompt)
        self._worker.moveToThread(self._gemini_thread)
        self._gemini_thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_ai_response)
        self._worker.finished.connect(self._gemini_thread.quit)
        self._gemini_thread.finished.connect(self._worker.deleteLater)
        self._gemini_thread.finished.connect(self._gemini_thread.deleteLater)
        self._gemini_thread.start()

    @pyqtSlot(object, object)
    def _on_ai_response(self, parsed, error):
        self.send_btn.setEnabled(True)

        if error:
            self._chat('SYS', f'AI 오류: {error}')
            return
        if not parsed:
            self._chat('SYS', 'AI 응답을 파싱할 수 없습니다.')
            return

        msg = parsed.get('message', '')
        if msg:
            self._chat('AI', msg)
            self._log_session('chat', {'role': 'ai', 'text': msg})

        for action in parsed.get('actions', []):
            self._execute_action(action)

    # ── 세션 관리 ──

    def _init_session(self, shp_dir):
        """새 세션 초기화 및 폴더 생성"""
        out_dir = self.out_path.text().strip() or shp_dir
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        session_dir = os.path.join(out_dir, 'sessions', ts)
        try:
            os.makedirs(session_dir, exist_ok=True)
            self._session_path = os.path.join(session_dir, 'session.json')
            self._session = {
                'started_at': ts,
                'shp_dir': shp_dir,
                'layers': [],
                'log': []
            }
        except Exception:
            self._session = None
            self._session_path = None

    def _log_session(self, event_type, data):
        """세션 로그에 이벤트 추가 후 저장"""
        if self._session is None:
            return
        self._session['log'].append({
            'time': datetime.now().strftime('%H:%M:%S'),
            'type': event_type,
            'data': data
        })
        try:
            with open(self._session_path, 'w', encoding='utf-8') as f:
                json.dump(self._session, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ── 액션 실행 ──

    def _snapshot(self, layer):
        """실행 전 renderer 스냅샷 저장 (최대 10단계)"""
        if layer.renderer():
            self._undo_stack.append((layer, layer.renderer().clone()))
            if len(self._undo_stack) > 10:
                self._undo_stack.pop(0)
            self.undo_btn.setEnabled(True)

    def _undo(self):
        """마지막 스타일 변경 되돌리기"""
        if not self._undo_stack:
            self._chat('SYS', '되돌릴 내용이 없습니다.')
            return
        layer, renderer = self._undo_stack.pop()
        layer.setRenderer(renderer)
        layer.triggerRepaint()
        self._chat('SYS', f'↩ {layer.name()} 이전 스타일로 복원')
        self._log_session('undo', {'layer': layer.name()})
        if not self._undo_stack:
            self.undo_btn.setEnabled(False)

    def _execute_action(self, action):
        atype = action.get('type')
        layer_name = action.get('layer', '')

        target = None
        for lyr in self.loaded_layers:
            if lyr.name() == layer_name or layer_name in lyr.name() or lyr.name() in layer_name:
                target = lyr
                break

        if atype == 'graduated':
            if not target:
                self._chat('SYS', f'레이어 없음: {layer_name}')
                return
            self._snapshot(target)
            ok = apply_graduated_style(
                target,
                action.get('field', ''),
                action.get('color_start', '#ffffff'),
                action.get('color_end', '#cc0000'),
                action.get('mode', 'quantile')
            )
            self._chat('SYS', f'✓ {target.name()} 등급 분류 적용' if ok else f'✗ {target.name()} 등급 분류 실패')
            if ok:
                self._log_session('action', action)

        elif atype == 'categorized':
            if not target:
                self._chat('SYS', f'레이어 없음: {layer_name}')
                return
            self._snapshot(target)
            ok = apply_categorized_style(target, action.get('field', ''), action.get('categories', {}))
            self._chat('SYS', f'✓ {target.name()} 범주 분류 적용' if ok else f'✗ {target.name()} 범주 분류 실패')
            if ok:
                self._log_session('action', action)

        elif atype == 'single':
            if not target:
                self._chat('SYS', f'레이어 없음: {layer_name}')
                return
            self._snapshot(target)
            apply_single_style(target, {
                'fill_color': action.get('fill_color', '#F5F5F5'),
                'stroke_color': action.get('stroke_color', '#9E9E9E'),
                'opacity': action.get('opacity', 0.8),
                'stroke_width': action.get('stroke_width', 0.26),
            })
            self._chat('SYS', f'✓ {target.name()} 색상 변경')
            self._log_session('action', action)

        elif atype == 'export':
            fmt = action.get('format', 'pdf')
            out_dir = self.out_path.text().strip() or self.shp_path.text().strip()
            out_name = self.out_name.text().strip() or 'pestomap_output'
            output_path = os.path.join(out_dir, out_name + ('.pdf' if fmt == 'pdf' else '.png'))
            try:
                ok = create_print_layout(self.iface, output_path, fmt, self.config, self.loaded_layers)
                self._chat('SYS', f'✓ 출력 완료: {output_path}' if ok else '✗ 출력 실패')
            except Exception as e:
                self._chat('SYS', f'✗ 출력 오류: {e}')

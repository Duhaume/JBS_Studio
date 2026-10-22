"""
Lecteur de scenes video/audio avec sortie sur second ecran.

- 41 scenes numerotees (0 a 40).
- Chaque scene a un media principal (video ou audio) ET un audio secondaire
  optionnel (ex: musique de fond) qui jouent simultanement.
- Chacune des deux pistes a sa propre case "Muet" et son propre curseur de
  volume (0-100%), modifiables en direct.
- Fenetre de sortie plein ecran sur le second moniteur (ce que voit le public).
- Apercu local muet dans la fenetre de controle (piste video principale
  uniquement).
- Barre espace : avance et joue la prochaine scene assignee. Retour arriere possible.
- API HTTP integree pour piloter a distance (Stream Deck, pupitre tactile, etc.).
- Sauvegarde/chargement du show dans un fichier .json.

Dependances :
    pip install python-vlc PyQt5
    VLC Media Player installe sur le systeme (meme architecture que Python,
    64 bits avec Python 64 bits).

Lancement :
    python media_scene_player.py

API HTTP (port 8080 par defaut) :
    GET /scene/<n>              -> joue la scene numero n
    GET /next                   -> avance a la prochaine scene assignee (= barre espace)
    GET /previous                -> revient a la scene assignee precedente
    GET /stop                   -> arrete la lecture
    GET /status                 -> statut JSON
    GET /mute/primary/on|off    -> coupe/retablit le son du media principal en cours
    GET /mute/secondary/on|off  -> coupe/retablit le son de l'audio secondaire en cours
    GET /volume/primary/<0-100>    -> regle le volume du media principal en cours
    GET /volume/secondary/<0-100>  -> regle le volume de l'audio secondaire en cours
"""

import json
import os
import platform
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import urllib.request
from urllib.parse import urlparse

import vlc
from PyQt5.QtCore import Qt, QObject, QPointF, QRectF, QSettings, QSize, QTimer, pyqtSignal
from PyQt5.QtGui import QGuiApplication, QIcon, QPainter, QPixmap, QKeySequence, QBrush, QColor, QPalette, QPen
from PyQt5.QtWidgets import (
    QAbstractItemView, QAction, QApplication, QCheckBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QShortcut, QSizePolicy, QSlider, QToolButton,
    QStyle, QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout, QWidget
)

NUM_SCENES_DEFAULT = 40
API_PORT = 8080

NUMBER_ROLE = Qt.UserRole + 1
PATH_ROLE = Qt.UserRole + 2
AUDIO_PATH_ROLE = Qt.UserRole + 3
MUTE_PRIMARY_ROLE = Qt.UserRole + 4
MUTE_SECONDARY_ROLE = Qt.UserRole + 5
VOLUME_PRIMARY_ROLE = Qt.UserRole + 6
VOLUME_SECONDARY_ROLE = Qt.UserRole + 7
NAME_ROLE = Qt.UserRole + 8
PLAYLIST_ROW_ROLE = Qt.UserRole + 9
AUTO_NEXT_ROLE = Qt.UserRole + 10
IS_PLAYING_ROLE = Qt.UserRole + 11

# --- Pupitre virtuel (reproduit la page 10 du Stream Deck XL : 8 x 4) ---
# Colonne 0 des 3 premieres lignes : UP / page / DOWN ; le reste : scenes 1 a 21
# (le numero seul, comme sur le vrai Stream Deck).
# Derniere ligne : PF, Scene Off, Salle on/off, Salle 20%, Noir Silence,
# Plein Ecran, Precedente, GO.
DECK_SCENES_PER_PAGE = 21
# Option Companion (touches de lumiere du boitier), DESACTIVEE par defaut.
# Les 4 touches PF / Scene Off / Salle on/off / Salle 20% sont toujours AFFICHEES
# (les classes s'entrainent sur le boitier) mais, desactivee, elles n'ont aucune
# action : pas de menu "Adresse de Companion...", aucune connexion reseau.
# Mettre True (ou lancer avec JBS_COMPANION=1) pour qu'elles agissent vraiment.
COMPANION_ENABLED = os.environ.get("JBS_COMPANION", "").lower() in ("1", "true", "yes")
COMPANION_DEFAULT_ADDRESS = "10.37.4.10:8000"   # hote:port de Bitfocus Companion
COMPANION_PAGE = 10                              # page du pupitre dans Companion
# Touches de la derniere ligne : (colonne, ligne1, ligne2, action)
#   "companion" = appui du bouton 10/3/<colonne> dans Companion (lumieres, via HA)
#   autres      = commande locale JBS Studio
DECK_BOTTOM = [
    (0, "PF", "", "companion"),
    (1, "Scene", "Off", "companion"),
    (2, "Salle", "on/off", "companion"),
    (3, "Salle", "20%", "companion"),
    (4, "Noir", "Silence", "stop"),
    (5, "Plein", "Ecran", "fullscreen"),
    (6, "\u25c0", "Prec.", "previous"),
    (7, "GO", "", "next"),
]

CURRENT_SCENE_COLOR = "#2e7d32"  # vert : scene en cours de lecture

LOCKED_SCENE_NUMBER = "0"  # scene reservee : pas de media assignable

FILE_CACHING_MS = "800"  # cache VLC (ms) pour lisser le demarrage de lecture
PREWARM_BYTES = 8 * 1024 * 1024  # taille lue en avance pour "chauffer" le cache disque

MEDIA_FILTERS = (
    "Fichiers media (*.mp4 *.mov *.avi *.mkv *.wmv *.m4v *.webm *.flv "
    "*.mp3 *.wav *.flac *.aac *.m4a *.ogg *.wma);;Tous les fichiers (*.*)"
)


def force_black_background(widget):
    """Garantit un fond noir repeint par Qt (utile quand VLC ne dessine plus
    rien dans le widget : audio seul, scene vide, arret)."""
    widget.setAutoFillBackground(True)
    palette = widget.palette()
    palette.setColor(QPalette.Window, QColor("black"))
    widget.setPalette(palette)


def format_ms(ms):
    total_seconds = max(0, ms) // 1000
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:02d}"


def scene_label(number, path, audio_path=None, name=None):
    parts = []
    if path:
        parts.append(os.path.basename(path))
    if audio_path:
        parts.append("\U0001F3B5 " + os.path.basename(audio_path))
    media_text = " + ".join(parts) if parts else "(vide)"
    if name:
        return f"{number}. {name} \u2014 {media_text}"
    return f"{number} \u2014 {media_text}"


def _draw_icon(kind, color, size=32):
    """Dessine une icone vectorielle (pas d'emoji : ils s'affichent en carre
    quand aucune police d'emoji n'est installee)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    c = QColor(color)
    pen = p.pen()
    pen.setColor(c)
    pen.setWidthF(2.4)
    pen.setJoinStyle(Qt.RoundJoin)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    if kind in ("lock_open", "lock_closed"):
        p.setBrush(c)
        p.drawRoundedRect(QRectF(7, 14, 18, 13), 2.5, 2.5)          # corps
        p.setBrush(Qt.NoBrush)
        if kind == "lock_closed":
            p.drawArc(QRectF(10, 4, 12, 15), 0, 180 * 16)             # anse fermee
            p.drawLine(QPointF(10, 11.5), QPointF(10, 14))
            p.drawLine(QPointF(22, 11.5), QPointF(22, 14))
        else:
            p.drawArc(QRectF(10, 4, 12, 15), 0, 180 * 16)             # anse ouverte
            p.drawLine(QPointF(10, 11.5), QPointF(10, 14))
    elif kind == "floppy":
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(5, 5, 22, 22), 2.5, 2.5)
        p.setBrush(c)
        p.drawRect(QRectF(10, 5, 11, 7))                              # volet metallique
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(9, 17, 14, 10))                             # etiquette
    elif kind == "screen":
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(4, 6, 24, 16), 2, 2)
        p.drawLine(QPointF(16, 22), QPointF(16, 27))
        p.drawLine(QPointF(10, 27), QPointF(22, 27))
    elif kind == "fullscreen":
        for (x, y, dx, dy) in ((5, 5, 1, 1), (27, 5, -1, 1), (5, 27, 1, -1), (27, 27, -1, -1)):
            p.drawLine(QPointF(x, y), QPointF(x + 8 * dx, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + 8 * dy))
    elif kind == "deck":
        p.setBrush(Qt.NoBrush)
        for r in range(3):
            for col in range(3):
                p.drawRoundedRect(QRectF(5 + col * 8.5, 5 + r * 8.5, 6, 6), 1.2, 1.2)
    p.end()
    return pm


def make_icon(kind, off_color="#333333", on_kind=None, on_color="#ffffff"):
    """Icone a deux etats (Off = bouton au repos, On = bouton coche/colore)."""
    icon = QIcon()
    icon.addPixmap(_draw_icon(kind, off_color), QIcon.Normal, QIcon.Off)
    icon.addPixmap(_draw_icon(on_kind or kind, on_color), QIcon.Normal, QIcon.On)
    return icon


class PlaylistWidget(QListWidget):
    """Liste des scenes (onglet Edition). Glisser-deposer INTERNE = reordonne
    les scenes (l'ordre de la liste = l'ordre de lecture GO/espace).
    Glisser-depose de fichiers EXTERNES depuis l'explorateur directement sur
    une ligne = assigne le MEDIA PRINCIPAL a cette scene (et aux suivantes si
    plusieurs fichiers sont deposes d'un coup ; la scene reservee est sautee).
    """
    contentChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setStyleSheet(
            "QListWidget::item { padding: 12px 10px; font-size: 13px; }"
        )
        self._drag_hover_item = None

    def _clear_drag_hover(self):
        if self._drag_hover_item is not None:
            self._drag_hover_item.setBackground(QBrush())
            self._drag_hover_item = None

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            item = self.itemAt(event.pos())
            if item is not self._drag_hover_item:
                self._clear_drag_hover()
                self._drag_hover_item = item
                if item is not None and item.data(NUMBER_ROLE) != LOCKED_SCENE_NUMBER:
                    item.setBackground(QColor("#2c5f4c"))
            event.acceptProposedAction()
        else:
            self._clear_drag_hover()
            super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self._clear_drag_hover()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._clear_drag_hover()

        if not event.mimeData().hasUrls():
            # Glisser-depose interne : reordonnancement des scenes.
            super().dropEvent(event)
            self.contentChanged.emit()
            return

        target_item = self.itemAt(event.pos())
        start_row = self.row(target_item) if target_item else 0

        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.toLocalFile()]
        row = start_row
        for path in paths:
            while row < self.count() and self.item(row).data(NUMBER_ROLE) == LOCKED_SCENE_NUMBER:
                row += 1
            if row >= self.count():
                break
            item = self.item(row)
            item.setData(PATH_ROLE, path)
            item.setText(scene_label(
                item.data(NUMBER_ROLE), path, item.data(AUDIO_PATH_ROLE), item.data(NAME_ROLE)
            ))
            row += 1

        event.acceptProposedAction()
        self.contentChanged.emit()


class DropZoneLabel(QLabel):
    """Etiquette qui accepte le glisser-depose d'un fichier depuis
    l'explorateur. Emet fileDropped(chemin) avec le premier fichier depose."""
    fileDropped = pyqtSignal(str)

    def __init__(self, text=""):
        super().__init__(text)
        self.setAcceptDrops(True)
        self.setMinimumHeight(26)
        self._apply_style(False)

    def _apply_style(self, hover):
        border = "2px dashed #4a90d9" if hover else "1px dashed #555555"
        self.setStyleSheet(
            f"color: gray; padding: 4px; border: {border}; border-radius: 4px;"
        )

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            self._apply_style(True)
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragLeaveEvent(self, event):
        self._apply_style(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._apply_style(False)
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls:
                path = urls[0].toLocalFile()
                if path:
                    self.fileDropped.emit(path)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class SceneItemDelegate(QStyledItemDelegate):
    """Dessine la scene en cours de lecture sur fond vert, meme quand elle
    est aussi selectionnee (sinon la couleur de selection masquerait le vert).
    Si elle est selectionnee, un cadre bleu indique la selection."""

    def paint(self, painter, option, index):
        # Toujours dessiner comme si la fenetre etait active : sinon, quand
        # le focus est ailleurs (fenetre de sortie, autre appli), la
        # selection passe en palette "inactive" (grise, parfois invisible).
        opt = QStyleOptionViewItem(option)
        opt.state |= QStyle.State_Active
        if not index.data(IS_PLAYING_ROLE):
            super().paint(painter, opt, index)
            return
        selected = bool(option.state & QStyle.State_Selected)
        opt.state &= ~QStyle.State_Selected
        painter.fillRect(option.rect, QColor(CURRENT_SCENE_COLOR))
        opt.palette.setColor(QPalette.Text, QColor("white"))
        super().paint(painter, opt, index)
        if selected:
            painter.save()
            painter.setPen(QPen(QColor("#4a90d9"), 3))
            painter.drawRect(option.rect.adjusted(1, 1, -2, -2))
            painter.restore()


class DeckWindow(QWidget):
    """Pupitre virtuel : copie de la page 10 du Stream Deck XL (8 x 4).
    - Touches 1 a 21 : numero seul ; lancent la scene par son NUMERO (/scene/<n>).
    - UP / DOWN : changent de page de scenes.
    - Prec. / GO : /previous et /next ; GO affiche la scene en cours.
    - Noir Silence : stop ; Plein Ecran : plein ecran de la sortie.
    - PF, Scene Off, Salle on/off, Salle 20% : affichees mais SANS action, sauf si
      COMPANION_ENABLED : alors appui du bouton correspondant dans Companion
      (POST /api/location/<page>/<ligne>/<colonne>/press, Companion >= 3).
    """
    pressResult = pyqtSignal(str, bool)

    def __init__(self, ctrl):
        super().__init__(ctrl)
        self.ctrl = ctrl
        self.page = 0
        self._last_index = -2
        self._signature = None
        self.settings = QSettings("JBS", "Studio")
        self.setWindowTitle("Streamdeck (boitier)")
        self.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setStyleSheet(
            "DeckWindow { background: #1b1b1b; }"
            "QPushButton { background: #2d2d2d; color: white; border: 1px solid #444;"
            " border-radius: 8px; font-weight: bold; }"
            "QPushButton:hover { border-color: #888; }"
            "QPushButton:pressed { background: #555; }"
            "QPushButton[ds=nav] { background: #1f3b5f; }"
            "QPushButton[ds=empty] { background: #222; color: #666; }"
            "QPushButton[ds=playing] { background: #2e7d32; border-color: #7bd88f; }"
            "QPushButton[ds=go] { background: #1f5f2b; }"
            "QPushButton[ds=warn] { background: #b36b00; }"
            "QPushButton[ds=stop] { background: #8b1f1f; }"
            "QPushButton[ds=light] { background: #5f4b1f; }"
            "QPushButton[ds=screen_on] { background: #1f5f2b; }"
            "QPushButton[ds=screen_off] { background: #555; color: #ddd; }"
        )
        layout = QVBoxLayout(self)
        grid = QGridLayout()
        grid.setSpacing(8)

        def key(text, row, col, style="", slot=None):
            btn = QPushButton(text)
            btn.setMinimumSize(92, 80)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            btn.setProperty("ds", style)
            if slot is not None:
                btn.clicked.connect(lambda _=False: slot())
            grid.addWidget(btn, row, col)
            return btn

        self.btn_up = key("UP", 0, 0, "nav", lambda: self.change_page(-1))
        self.btn_page = key("", 1, 0, "nav")
        self.btn_down = key("DOWN", 2, 0, "nav", lambda: self.change_page(1))
        self.scene_buttons = []
        for r in range(3):
            for c in range(1, 8):
                k = r * 7 + (c - 1)
                self.scene_buttons.append(
                    key("", r, c, "", lambda k=k: self._on_scene_clicked(k)))

        self.btn_go = None
        for col, l1, l2, action in DECK_BOTTOM:
            text = l1 + ("\n" + l2 if l2 else "")
            style = {"companion": "light", "stop": "stop", "next": "go"}.get(action, "")
            if action == "companion":
                # Option Companion desactivee : la touche est affichee (pour que les
                # classes s'entrainent) mais n'a AUCUNE action.
                slot = (lambda c=col, t=text: self.press_companion(c, t.replace("\n", " "))) \
                    if COMPANION_ENABLED else None
            elif action == "stop":
                slot = ctrl.stop_playback
            elif action == "fullscreen":
                slot = ctrl.toggle_output_fullscreen
            elif action == "previous":
                slot = ctrl.go_previous
            else:
                slot = ctrl.go_next
            btn = key(text, 3, col, style, slot)
            if action == "next":
                self.btn_go = btn

        layout.addLayout(grid)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #aaa; font-size: 11px;")
        self.status.setFixedHeight(16)
        layout.addWidget(self.status)
        self.status.setVisible(COMPANION_ENABLED)
        self.pressResult.connect(self._on_result)

    def resizeEvent(self, event):
        # Le texte des touches suit la taille de la fenetre.
        size = max(12, min(34, int(self.height() / 18)))
        for btn in self.findChildren(QPushButton):
            f = btn.font()
            f.setPixelSize(size)
            btn.setFont(f)
        super().resizeEvent(event)

    # --- Companion (touches lumiere) --------------------------------------
    def address(self):
        return str(self.settings.value("companion_address", COMPANION_DEFAULT_ADDRESS))

    def ask_address(self):
        text, ok = QInputDialog.getText(
            self, "Adresse de Companion", "Adresse (hote:port) :", text=self.address())
        text = text.strip()
        host, _, port = text.rpartition(":")
        if not ok or not text:
            return
        if not host or not port.isdigit():
            QMessageBox.warning(self, "Adresse invalide", "Format attendu : hote:port (ex. 10.37.4.10:8000)")
            return
        self.settings.setValue("companion_address", text)
        self.status.setText("Adresse enregistree")

    def press_companion(self, col, name):
        url = f"http://{self.address()}/api/location/{COMPANION_PAGE}/3/{col}/press"
        threading.Thread(target=self._send, args=(url, name), daemon=True).start()

    def _send(self, url, name):
        # Thread separe : ne bloque jamais l'interface si Companion ne repond pas.
        try:
            req = urllib.request.Request(url, data=b"", method="POST")
            with urllib.request.urlopen(req, timeout=2) as resp:
                ok = 200 <= resp.status < 300
            self.pressResult.emit(name, ok)
        except Exception:
            self.pressResult.emit(name, False)

    def _on_result(self, name, ok):
        if ok:
            self.status.setText(f"\u2713 {name} envoye a Companion")
        else:
            self.status.setText(f"\u2717 {name} : Companion injoignable ({self.address()})")

    # --- scenes -----------------------------------------------------------
    def _numbers(self):
        """numero (int) -> ligne, pour les scenes dont le numero est un entier >= 1."""
        out = {}
        pl = self.ctrl.playlist
        for row in range(pl.count()):
            n = pl.item(row).data(NUMBER_ROLE)
            if n and str(n).isdigit() and int(n) >= 1:
                out[int(n)] = row
        return out

    def _page_count(self):
        nums = self._numbers()
        top = max(nums) if nums else 1
        return max(1, -(-top // DECK_SCENES_PER_PAGE))

    def change_page(self, delta):
        self.page = max(0, min(self._page_count() - 1, self.page + delta))
        self._signature = None
        self.refresh()

    def _on_scene_clicked(self, k):
        n = self.page * DECK_SCENES_PER_PAGE + k + 1
        row = self._numbers().get(n)
        if row is not None:
            self.ctrl.play_scene(row)

    @staticmethod
    def _set_style(btn, style):
        if btn.property("ds") != style:
            btn.setProperty("ds", style)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def refresh(self):
        c = self.ctrl
        pl = c.playlist
        count = pl.count()
        if c.current_index != self._last_index:
            self._last_index = c.current_index
            if 0 <= c.current_index < count:
                n = pl.item(c.current_index).data(NUMBER_ROLE)
                if n and str(n).isdigit() and int(n) >= 1:
                    self.page = (int(n) - 1) // DECK_SCENES_PER_PAGE
        pages = self._page_count()
        self.page = max(0, min(pages - 1, self.page))
        nums = self._numbers()
        cur = pl.item(c.current_index) if 0 <= c.current_index < count else None

        cells = []
        for k in range(DECK_SCENES_PER_PAGE):
            n = self.page * DECK_SCENES_PER_PAGE + k + 1
            row = nums.get(n)
            if row is None:
                cells.append(("", "empty"))
                continue
            item = pl.item(row)
            has_media = bool(item.data(PATH_ROLE) or item.data(AUDIO_PATH_ROLE))
            style = "playing" if row == c.current_index else ("" if has_media else "empty")
            cells.append((str(n), style))          # numero seul, jamais le nom
        if cur is not None:
            go_text = f"GO\n\u25b6 {cur.data(NUMBER_ROLE)}"
            go_style = "go" if (cur.data(PATH_ROLE) or cur.data(AUDIO_PATH_ROLE)) else "warn"
        else:
            go_text, go_style = "GO", "go"

        sig = (self.page, pages, tuple(cells), go_text, go_style)
        if sig == self._signature:
            return
        self._signature = sig
        for btn, (text, style) in zip(self.scene_buttons, cells):
            btn.setText(text)
            self._set_style(btn, style)
        self.btn_go.setText(go_text)
        self._set_style(self.btn_go, go_style)
        self.btn_page.setText(f"PAGE\n{self.page + 1}/{pages}")
        self.btn_up.setEnabled(self.page > 0)
        self.btn_down.setEnabled(self.page < pages - 1)


class OutputWindow(QWidget):
    """Fenetre plein ecran destinee au second ecran (sortie publique)."""

    def __init__(self):
        super().__init__()
        self.control_window = None
        self.setWindowTitle("Sortie")
        # Toujours au premier plan (a definir avant la creation de la fenetre native
        # que VLC utilise : changer les flags plus tard la recreerait).
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setStyleSheet("background-color: black;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.video_frame = QFrame(self)
        force_black_background(self.video_frame)
        self.video_frame.setStyleSheet("background-color: black;")
        layout.addWidget(self.video_frame)

    def keyPressEvent(self, event):
        if self.control_window is not None:
            self.control_window.handle_key(event)
        else:
            super().keyPressEvent(event)


class Bridge(QObject):
    """Pont thread-safe entre le serveur HTTP (thread separe) et l'interface
    Qt (thread principal)."""
    goto_number = pyqtSignal(str)
    next_scene = pyqtSignal()
    previous_scene = pyqtSignal()
    stop_scene = pyqtSignal()
    mute_primary = pyqtSignal(bool)
    mute_secondary = pyqtSignal(bool)
    volume_primary = pyqtSignal(int)
    volume_secondary = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.window = None

    def get_status(self):
        if self.window is None:
            return {"error": "not_ready"}
        w = self.window
        idx = w.current_index
        if 0 <= idx < w.playlist.count():
            item = w.playlist.item(idx)
            current = {
                "number": item.data(NUMBER_ROLE),
                "path": item.data(PATH_ROLE) or None,
                "audio_path": item.data(AUDIO_PATH_ROLE) or None,
                "mute_primary": bool(item.data(MUTE_PRIMARY_ROLE)),
                "mute_secondary": bool(item.data(MUTE_SECONDARY_ROLE)),
                "volume_primary": item.data(VOLUME_PRIMARY_ROLE),
                "volume_secondary": item.data(VOLUME_SECONDARY_ROLE),
            }
        else:
            current = None
        return {"current_scene": current, "total_scenes": w.playlist.count()}


def make_http_handler(bridge):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, data, status=200):
            body = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            parts = [p for p in parsed.path.split("/") if p]

            if parsed.path == "/status":
                self._json(bridge.get_status())
            elif parsed.path == "/next":
                bridge.next_scene.emit()
                self._json({"ok": True})
            elif parsed.path == "/previous":
                bridge.previous_scene.emit()
                self._json({"ok": True})
            elif parsed.path == "/stop":
                bridge.stop_scene.emit()
                self._json({"ok": True})
            elif len(parts) == 2 and parts[0] == "scene":
                bridge.goto_number.emit(parts[1])
                self._json({"ok": True, "scene": parts[1]})
            elif len(parts) == 3 and parts[0] == "mute" and parts[1] in ("primary", "secondary") and parts[2] in ("on", "off"):
                muted = parts[2] == "on"
                if parts[1] == "primary":
                    bridge.mute_primary.emit(muted)
                else:
                    bridge.mute_secondary.emit(muted)
                self._json({"ok": True, "layer": parts[1], "muted": muted})
            elif len(parts) == 3 and parts[0] == "volume" and parts[1] in ("primary", "secondary") and parts[2].isdigit():
                value = max(0, min(100, int(parts[2])))
                if parts[1] == "primary":
                    bridge.volume_primary.emit(value)
                else:
                    bridge.volume_secondary.emit(value)
                self._json({"ok": True, "layer": parts[1], "volume": value})
            else:
                self._json(
                    {
                        "error": "not_found",
                        "routes": [
                            "/scene/<n>", "/next", "/previous", "/stop", "/status",
                            "/mute/primary/on|off", "/mute/secondary/on|off",
                            "/volume/primary/<0-100>", "/volume/secondary/<0-100>"
                        ]
                    },
                    404
                )

        def log_message(self, format, *args):
            pass

    return Handler


class ControlWindow(QMainWindow):
    media_end_reached = pyqtSignal()  # emis (thread VLC) quand le media principal se termine

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Controle de scenes")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)  # JBS Studio reste au premier plan
        self.resize(680, 920)

        self.current_index = -1
        self.current_show_path = None
        self.clipboard_scene = None

        # --- VLC : lecteurs independants ---
        # preview  : media principal, muet, pour le monitoring local
        # program  : media principal, sortie publique
        # audio_2  : audio secondaire (musique/voix off), sortie publique uniquement
        self.vlc_instance = vlc.Instance()
        self.preview_player = self.vlc_instance.media_player_new()
        self.program_player = self.vlc_instance.media_player_new()
        self.program_audio_player = self.vlc_instance.media_player_new()
        self.preview_player.audio_set_mute(True)

        # Enchainement automatique : quand la piste principale se termine,
        # on avance a la scene suivante si la case "Enchainer" est cochee.
        self.media_end_reached.connect(self._on_media_end_reached)
        self.program_player.event_manager().event_attach(
            vlc.EventType.MediaPlayerEndReached, self._vlc_end_callback
        )

        # --- Fenetre de sortie (second ecran) ---
        self.output_window = OutputWindow()
        self.output_window.control_window = self

        # --- Interface ---
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)

        self.show_locked = False

        # --- Barre d'outils : cadenas / disquette / ecran de sortie ---
        toolbar = QHBoxLayout()
        self.btn_lock = QToolButton()
        self.btn_lock.setCheckable(True)
        self.btn_lock.setText("Verrouillage")
        self.btn_lock.setIcon(make_icon("lock_open", on_kind="lock_closed"))
        self.btn_lock.setToolTip("Verrouiller le spectacle (bloque l'edition)")
        self.btn_lock.toggled.connect(self.set_show_locked)
        self.btn_save = QToolButton()
        self.btn_save.setText("Enregistrer")
        self.btn_save.setIcon(make_icon("floppy"))
        self.btn_save.setToolTip("Enregistrer le show (Ctrl+S)")
        self.btn_save.clicked.connect(self.save_show)
        self.btn_screen = QToolButton()
        self.btn_screen.setCheckable(True)
        self.btn_screen.setChecked(True)
        self.btn_screen.setText("Sortie")
        self.btn_screen.setIcon(make_icon("screen", off_color="#dddddd"))
        self.btn_screen.setToolTip("Activer / desactiver l'ecran de sortie")
        self.btn_fullscreen = QToolButton()
        self.btn_fullscreen.setText("Plein ecran")
        self.btn_fullscreen.setIcon(make_icon("fullscreen"))
        self.btn_fullscreen.setToolTip("Plein ecran de la sortie on/off (F)")
        self.btn_fullscreen.clicked.connect(lambda: self.toggle_output_fullscreen())
        self.deck_window = DeckWindow(self)  # pupitre de scenes
        self.btn_deck = QToolButton()
        self.btn_deck.setCheckable(True)
        self.btn_deck.setText("Streamdeck\n(boitier)")
        self.btn_deck.setIcon(make_icon("deck"))
        self.btn_deck.setToolTip("Afficher / masquer le Streamdeck (boitier)")
        self.btn_deck.toggled.connect(self._on_deck_toggled)
        for b_ in (self.btn_lock, self.btn_save, self.btn_screen, self.btn_fullscreen, self.btn_deck):
            b_.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b_.setIconSize(QSize(28, 28))
            b_.setStyleSheet("QToolButton { font-size: 13px; padding: 4px 10px; min-width: 70px; }"
                             "QToolButton:checked { background-color: #b71c1c; }")
            toolbar.addWidget(b_)
        self.btn_deck.setStyleSheet("QToolButton { font-size: 13px; padding: 4px 10px; min-width: 70px; }"
                                    "QToolButton:checked { background-color: #2e7d32; }")
        self.btn_fullscreen.setStyleSheet("QToolButton { font-size: 13px; padding: 4px 10px; min-width: 70px; }")
        self.btn_screen.setStyleSheet("QToolButton { font-size: 13px; padding: 4px 10px; min-width: 70px; }"
                                      "QToolButton:checked { background-color: #2e7d32; color: white; }"
                                      "QToolButton:!checked { background-color: #555; color: #ddd; }")
        toolbar.addStretch()
        outer.addLayout(toolbar)

        show_layout = outer
        edit_layout = outer

        self.preview_frame = QFrame()
        force_black_background(self.preview_frame)
        self.preview_frame.setMinimumHeight(170)
        self.preview_frame.setStyleSheet("background-color: black;")
        show_layout.addWidget(self.preview_frame)

        status_row = QHBoxLayout()
        self.status_label = QLabel("Aucune scene en cours")
        self.time_label = QLabel("00:00 / 00:00")
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        status_row.addWidget(self.time_label)
        show_layout.addLayout(status_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        show_layout.addWidget(self.progress_bar)

        self.playlist = PlaylistWidget()
        self.playlist.setMinimumHeight(220)
        self.playlist.setItemDelegate(SceneItemDelegate(self.playlist))
        pal = self.playlist.palette()
        for role in (QPalette.Highlight, QPalette.HighlightedText):
            pal.setColor(QPalette.Inactive, role, pal.color(QPalette.Active, role))
        self.playlist.setPalette(pal)
        self.playlist.itemDoubleClicked.connect(lambda item: self.play_scene(self.playlist.row(item)))
        self.playlist.currentRowChanged.connect(self.on_selection_changed)
        # Clic simple = selection ; double-clic ou Entree = jouer.
        for key in (Qt.Key_Return, Qt.Key_Enter):
            sc = QShortcut(QKeySequence(key), self.playlist)
            sc.setContext(Qt.WidgetShortcut)
            sc.activated.connect(self.play_selected)
        show_layout.addWidget(self.playlist)

        show_transport = QHBoxLayout()
        btn_prev_show = QPushButton("\u25c0 Precedente")
        btn_go_show = QPushButton("\u25b6 GO (espace)")
        btn_go_show.setStyleSheet("font-weight: bold; padding: 8px;")
        btn_stop_show = QPushButton("\u25a0 Stop")
        btn_prev_show.clicked.connect(self.go_previous)
        btn_go_show.clicked.connect(self.go_next)
        btn_stop_show.clicked.connect(self.stop_playback)
        show_transport.addWidget(btn_prev_show)
        show_transport.addWidget(btn_go_show)
        show_transport.addWidget(btn_stop_show)
        show_layout.addLayout(show_transport)

        self.api_label = QLabel("")
        self.api_label.setStyleSheet("color: gray; font-size: 11px;")
        show_layout.addWidget(self.api_label)

        # --- Details de la scene selectionnee ---
        copy_paste_row = QHBoxLayout()
        self.btn_copy_scene = QPushButton("Copier la scene")
        self.btn_paste_scene = QPushButton("Coller dans la scene")
        self.btn_copy_scene.clicked.connect(self.copy_selected_scene)
        self.btn_paste_scene.clicked.connect(self.paste_scene_to_selected)
        copy_paste_row.addWidget(self.btn_copy_scene)
        copy_paste_row.addWidget(self.btn_paste_scene)
        copy_paste_row.addStretch()
        self.clipboard_status_label = QLabel("Presse-papiers : vide")
        self.clipboard_status_label.setStyleSheet("color: gray; font-size: 11px;")
        copy_paste_row.addWidget(self.clipboard_status_label)
        edit_layout.addLayout(copy_paste_row)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Nom de la scene :"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("(optionnel)")
        self.name_edit.editingFinished.connect(self.on_name_edited)
        name_row.addWidget(self.name_edit, 1)
        edit_layout.addLayout(name_row)

        primary_row = QHBoxLayout()
        primary_row.addWidget(QLabel("Media principal :"))
        self.detail_primary_label = DropZoneLabel("(vide) \u2014 glisse un fichier ici")
        self.detail_primary_label.fileDropped.connect(self.on_primary_dropped)
        primary_row.addWidget(self.detail_primary_label, 1)
        self.btn_assign_primary = QPushButton("Assigner...")
        self.btn_clear_primary = QPushButton("Vider")
        self.btn_assign_primary.clicked.connect(self.assign_primary_to_selected)
        self.btn_clear_primary.clicked.connect(self.clear_primary_selected)
        primary_row.addWidget(self.btn_assign_primary)
        primary_row.addWidget(self.btn_clear_primary)
        edit_layout.addLayout(primary_row)

        secondary_row = QHBoxLayout()
        secondary_row.addWidget(QLabel("Audio secondaire :"))
        self.detail_secondary_label = DropZoneLabel("(vide) \u2014 glisse un fichier ici")
        self.detail_secondary_label.fileDropped.connect(self.on_secondary_dropped)
        secondary_row.addWidget(self.detail_secondary_label, 1)
        self.btn_assign_secondary = QPushButton("Assigner...")
        self.btn_clear_secondary = QPushButton("Vider")
        self.btn_assign_secondary.clicked.connect(self.assign_secondary_to_selected)
        self.btn_clear_secondary.clicked.connect(self.clear_secondary_selected)
        secondary_row.addWidget(self.btn_assign_secondary)
        secondary_row.addWidget(self.btn_clear_secondary)
        edit_layout.addLayout(secondary_row)

        mute_row = QHBoxLayout()
        self.mute_primary_checkbox = QCheckBox("Muet (media principal)")
        self.mute_secondary_checkbox = QCheckBox("Muet (audio secondaire)")
        self.mute_primary_checkbox.toggled.connect(self.on_mute_primary_toggled)
        self.mute_secondary_checkbox.toggled.connect(self.on_mute_secondary_toggled)
        mute_row.addWidget(self.mute_primary_checkbox)
        mute_row.addWidget(self.mute_secondary_checkbox)
        edit_layout.addLayout(mute_row)

        auto_next_row = QHBoxLayout()
        self.auto_next_checkbox = QCheckBox("Enchainer automatiquement avec la scene suivante")
        self.auto_next_checkbox.toggled.connect(self.on_auto_next_toggled)
        auto_next_row.addWidget(self.auto_next_checkbox)
        edit_layout.addLayout(auto_next_row)

        volume_row = QHBoxLayout()
        volume_row.addWidget(QLabel("Volume principal :"))
        self.volume_primary_slider = QSlider(Qt.Horizontal)
        self.volume_primary_slider.setRange(0, 100)
        self.volume_primary_slider.setValue(100)
        self.volume_primary_slider.valueChanged.connect(self.on_volume_primary_changed)
        self.volume_primary_value_label = QLabel("100%")
        volume_row.addWidget(self.volume_primary_slider, 1)
        volume_row.addWidget(self.volume_primary_value_label)
        edit_layout.addLayout(volume_row)

        volume_row_2 = QHBoxLayout()
        volume_row_2.addWidget(QLabel("Volume secondaire :"))
        self.volume_secondary_slider = QSlider(Qt.Horizontal)
        self.volume_secondary_slider.setRange(0, 100)
        self.volume_secondary_slider.setValue(100)
        self.volume_secondary_slider.valueChanged.connect(self.on_volume_secondary_changed)
        self.volume_secondary_value_label = QLabel("100%")
        volume_row_2.addWidget(self.volume_secondary_slider, 1)
        volume_row_2.addWidget(self.volume_secondary_value_label)
        edit_layout.addLayout(volume_row_2)

        self.screen_actions = []
        self.build_menu()

        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.update_progress)
        self.timer.start()

        self.space_shortcut = QShortcut(QKeySequence(Qt.Key_Space), self)
        self.space_shortcut.activated.connect(self.go_next)
        self.back_shortcut = QShortcut(QKeySequence(Qt.Key_Backspace), self)
        self.back_shortcut.activated.connect(self.go_previous)
        self.save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        self.save_shortcut.activated.connect(self.save_show)
        self.copy_shortcut = QShortcut(QKeySequence("Ctrl+Shift+C"), self)
        self.copy_shortcut.activated.connect(self.copy_selected_scene)
        self.paste_shortcut = QShortcut(QKeySequence("Ctrl+Shift+V"), self)
        self.paste_shortcut.activated.connect(self.paste_scene_to_selected)
        self.mute_shortcut = QShortcut(QKeySequence("Ctrl+M"), self)
        self.mute_shortcut.activated.connect(self.toggle_current_primary_mute)
        self.mute_secondary_shortcut = QShortcut(QKeySequence("Ctrl+Shift+M"), self)
        self.mute_secondary_shortcut.activated.connect(self.toggle_current_secondary_mute)
        self.stop_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self.stop_shortcut.activated.connect(self.stop_playback)
        self.new_show_shortcut = QShortcut(QKeySequence("Ctrl+N"), self)
        self.new_show_shortcut.activated.connect(lambda: self.new_show(NUM_SCENES_DEFAULT))
        self.open_show_shortcut = QShortcut(QKeySequence("Ctrl+O"), self)
        self.open_show_shortcut.activated.connect(self.open_show)
        self.volume_primary_up_shortcut = QShortcut(QKeySequence("Ctrl+Up"), self)
        self.volume_primary_up_shortcut.activated.connect(lambda: self.adjust_current_primary_volume(5))
        self.volume_primary_down_shortcut = QShortcut(QKeySequence("Ctrl+Down"), self)
        self.volume_primary_down_shortcut.activated.connect(lambda: self.adjust_current_primary_volume(-5))
        self.volume_secondary_up_shortcut = QShortcut(QKeySequence("Shift+Up"), self)
        self.volume_secondary_up_shortcut.activated.connect(lambda: self.adjust_current_secondary_volume(5))
        self.volume_secondary_down_shortcut = QShortcut(QKeySequence("Shift+Down"), self)
        self.volume_secondary_down_shortcut.activated.connect(lambda: self.adjust_current_secondary_volume(-5))
        self.fullscreen_shortcut = QShortcut(QKeySequence("F"), self)
        self.fullscreen_shortcut.activated.connect(self.toggle_output_fullscreen)

        self.attach_video_outputs()
        self.new_show(NUM_SCENES_DEFAULT)
        self.start_api_server()

    # ------------------------------------------------------------------ #
    def build_menu(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("Fichier")
        action_new = QAction("Nouveau show (41 scenes vides, 0 a 40)", self)
        action_open = QAction("Ouvrir un show...", self)
        action_save = QAction("Enregistrer", self)
        action_save_as = QAction("Enregistrer sous...", self)
        action_new.triggered.connect(lambda: self.new_show(NUM_SCENES_DEFAULT))
        action_open.triggered.connect(self.open_show)
        action_save.triggered.connect(self.save_show)
        action_save_as.triggered.connect(self.save_show_as)
        self.action_new, self.action_open = action_new, action_open
        for a in (action_new, action_open, action_save, action_save_as):
            file_menu.addAction(a)

        deck_menu = menubar.addMenu("Streamdeck (boitier)")
        action_deck = QAction("Afficher le Streamdeck (boitier)", self, checkable=True)
        action_deck.toggled.connect(self.btn_deck.setChecked)
        self.btn_deck.toggled.connect(action_deck.setChecked)
        deck_menu.addAction(action_deck)
        if COMPANION_ENABLED:
            action_deck_addr = QAction("Adresse de Companion...", self)
            action_deck_addr.triggered.connect(self.deck_window.ask_address)
            deck_menu.addAction(action_deck_addr)

        screen_menu = menubar.addMenu("Ecran de sortie")

        self.toggle_output_action = QAction("Activer l'ecran de sortie", self, checkable=True)
        self.toggle_output_action.setChecked(True)
        self.toggle_output_action.toggled.connect(self.set_output_enabled)
        self.toggle_output_action.toggled.connect(self.btn_screen.setChecked)
        self.btn_screen.toggled.connect(self.toggle_output_action.setChecked)
        screen_menu.addAction(self.toggle_output_action)
        screen_menu.addSeparator()

        screens = QGuiApplication.screens()
        for i, screen in enumerate(screens):
            geo = screen.geometry()
            label = f"Ecran {i + 1} \u2014 {geo.width()}x{geo.height()}"
            action = QAction(label, self, checkable=True)
            action.triggered.connect(lambda checked, idx=i: self.send_output_to_screen(idx))
            screen_menu.addAction(action)
            self.screen_actions.append(action)
        screen_menu.addSeparator()
        fullscreen_action = QAction("Basculer plein ecran sortie", self)
        fullscreen_action.triggered.connect(self.toggle_output_fullscreen)
        screen_menu.addAction(fullscreen_action)

        self.selected_screen_index = 1 if len(screens) > 1 else 0
        self.send_output_to_screen(self.selected_screen_index)

    def set_output_enabled(self, enabled):
        if enabled:
            self.send_output_to_screen(self.selected_screen_index)
        else:
            self.output_window.hide()
        for action in self.screen_actions:
            action.setEnabled(enabled)
        self.btn_fullscreen.setEnabled(enabled)

    def send_output_to_screen(self, index):
        screens = QGuiApplication.screens()
        if index >= len(screens):
            return
        self.selected_screen_index = index
        geometry = screens[index].geometry()
        self.output_window.setGeometry(geometry)
        if len(screens) > 1:
            self.output_window.showFullScreen()
        else:
            # Un seul ecran detecte : pas de plein ecran automatique
            # (sinon la fenetre de sortie recouvrirait l'ecran de controle).
            self.output_window.showNormal()
            self.output_window.resize(800, 450)
        for i, action in enumerate(self.screen_actions):
            action.setChecked(i == index)

    def toggle_output_fullscreen(self):
        if not self.toggle_output_action.isChecked():
            return
        if self.output_window.isFullScreen():
            self.output_window.showNormal()
        else:
            self.output_window.showFullScreen()

    # ------------------------------------------------------------------ #
    def attach_video_outputs(self):
        # Associe chaque lecteur VLC a son widget Qt. La methode differe
        # selon l'OS : set_hwnd (Windows), set_xwindow (Linux/X11),
        # set_nsobject (macOS).
        system = platform.system()
        preview_id = int(self.preview_frame.winId())
        program_id = int(self.output_window.video_frame.winId())

        if system == "Windows":
            self.preview_player.set_hwnd(preview_id)
            self.program_player.set_hwnd(program_id)
        elif system == "Darwin":
            self.preview_player.set_nsobject(preview_id)
            self.program_player.set_nsobject(program_id)
        else:
            self.preview_player.set_xwindow(preview_id)
            self.program_player.set_xwindow(program_id)

    # ------------------------------------------------------------------ #
    def play_selected(self):
        # Entree : joue la scene selectionnee.
        row = self.playlist.currentRow()
        if row >= 0:
            self.play_scene(row)

    def set_show_locked(self, locked):
        """Cadenas : bloque l'edition, garde les commandes de jeu."""
        self.show_locked = locked
        # (l'icone cadenas ferme/ouvert suit l'etat coche du bouton)
        self.playlist.setAcceptDrops(not locked)
        self.playlist.setDragEnabled(not locked)
        self.playlist.setDragDropMode(
            QAbstractItemView.NoDragDrop if locked else QAbstractItemView.InternalMove)
        for act in (self.action_new, self.action_open):
            act.setEnabled(not locked)
        for sc in (self.new_show_shortcut, self.open_show_shortcut,
                   self.copy_shortcut, self.paste_shortcut):
            sc.setEnabled(not locked)
        self.on_selection_changed(self.playlist.currentRow())

    def _on_deck_toggled(self, visible):
        self.deck_window.setVisible(visible)
        if visible:
            self.deck_window.refresh()

    def update_current_highlight(self):
        color = QColor(CURRENT_SCENE_COLOR)
        lst = self.playlist
        for i in range(lst.count()):
            it = lst.item(i)
            playing = (i == self.current_index)
            it.setData(IS_PLAYING_ROLE, playing)
            it.setBackground(QBrush(color) if playing else QBrush())
        lst.viewport().update()
        if hasattr(self, "deck_window") and self.deck_window.isVisible():
            self.deck_window.refresh()

    def _vlc_end_callback(self, event):
        # Appele depuis un thread VLC : on ne fait qu'emettre un signal Qt,
        # qui sera traite en toute securite dans le thread principal.
        self.media_end_reached.emit()

    def _on_media_end_reached(self):
        if 0 <= self.current_index < self.playlist.count():
            item = self.playlist.item(self.current_index)
            if bool(item.data(AUTO_NEXT_ROLE)):
                self.go_next()

    # ------------------------------------------------------------------ #
    def new_show(self, count):
        self.playlist.clear()
        for i in range(0, count + 1):
            item = QListWidgetItem()
            item.setData(NUMBER_ROLE, str(i))
            item.setData(PATH_ROLE, "")
            item.setData(AUDIO_PATH_ROLE, "")
            item.setData(NAME_ROLE, "")
            item.setData(MUTE_PRIMARY_ROLE, False)
            item.setData(MUTE_SECONDARY_ROLE, False)
            item.setData(VOLUME_PRIMARY_ROLE, 100)
            item.setData(VOLUME_SECONDARY_ROLE, 100)
            item.setData(AUTO_NEXT_ROLE, False)
            item.setText(scene_label(str(i), "", ""))
            self.playlist.addItem(item)
        self.current_index = -1
        self.current_show_path = None
        self.status_label.setText("Aucune scene en cours")
        self.setWindowTitle("Controle de scenes \u2014 nouveau show")
        self.update_current_highlight()

    def _is_locked_row(self, row):
        item = self.playlist.item(row)
        return item is not None and item.data(NUMBER_ROLE) == LOCKED_SCENE_NUMBER

    def on_primary_dropped(self, path):
        row = self.playlist.currentRow()
        if row < 0:
            QMessageBox.information(
                self, "Aucune scene selectionnee",
                "Selectionne d'abord une scene dans la liste avant de glisser un fichier."
            )
            return
        if self._is_locked_row(row):
            QMessageBox.information(self, "Scene reservee", f"La scene {LOCKED_SCENE_NUMBER} ne peut pas recevoir de fichier.")
            return
        item = self.playlist.item(row)
        item.setData(PATH_ROLE, path)
        item.setText(scene_label(item.data(NUMBER_ROLE), path, item.data(AUDIO_PATH_ROLE), item.data(NAME_ROLE)))
        self.on_selection_changed(row)

    def on_secondary_dropped(self, path):
        row = self.playlist.currentRow()
        if row < 0:
            QMessageBox.information(
                self, "Aucune scene selectionnee",
                "Selectionne d'abord une scene dans la liste avant de glisser un fichier."
            )
            return
        if self._is_locked_row(row):
            QMessageBox.information(self, "Scene reservee", f"La scene {LOCKED_SCENE_NUMBER} ne peut pas recevoir de fichier.")
            return
        item = self.playlist.item(row)
        item.setData(AUDIO_PATH_ROLE, path)
        item.setText(scene_label(item.data(NUMBER_ROLE), item.data(PATH_ROLE), path, item.data(NAME_ROLE)))
        self.on_selection_changed(row)

    def assign_primary_to_selected(self):
        row = self.playlist.currentRow()
        if row < 0:
            QMessageBox.information(self, "Aucune scene selectionnee", "Selectionne d'abord une scene dans la liste.")
            return
        if self._is_locked_row(row):
            QMessageBox.information(self, "Scene reservee", f"La scene {LOCKED_SCENE_NUMBER} ne peut pas recevoir de fichier.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Assigner le media principal", "", MEDIA_FILTERS)
        if not path:
            return
        item = self.playlist.item(row)
        item.setData(PATH_ROLE, path)
        item.setText(scene_label(item.data(NUMBER_ROLE), path, item.data(AUDIO_PATH_ROLE), item.data(NAME_ROLE)))
        self.on_selection_changed(row)

    def assign_secondary_to_selected(self):
        row = self.playlist.currentRow()
        if row < 0:
            QMessageBox.information(self, "Aucune scene selectionnee", "Selectionne d'abord une scene dans la liste.")
            return
        if self._is_locked_row(row):
            QMessageBox.information(self, "Scene reservee", f"La scene {LOCKED_SCENE_NUMBER} ne peut pas recevoir de fichier.")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Assigner l'audio secondaire", "", MEDIA_FILTERS)
        if not path:
            return
        item = self.playlist.item(row)
        item.setData(AUDIO_PATH_ROLE, path)
        item.setText(scene_label(item.data(NUMBER_ROLE), item.data(PATH_ROLE), path, item.data(NAME_ROLE)))
        self.on_selection_changed(row)

    def clear_primary_selected(self):
        row = self.playlist.currentRow()
        if row < 0:
            return
        item = self.playlist.item(row)
        item.setData(PATH_ROLE, "")
        item.setText(scene_label(item.data(NUMBER_ROLE), "", item.data(AUDIO_PATH_ROLE), item.data(NAME_ROLE)))
        self.on_selection_changed(row)

    def clear_secondary_selected(self):
        row = self.playlist.currentRow()
        if row < 0:
            return
        item = self.playlist.item(row)
        item.setData(AUDIO_PATH_ROLE, "")
        item.setText(scene_label(item.data(NUMBER_ROLE), item.data(PATH_ROLE), "", item.data(NAME_ROLE)))
        self.on_selection_changed(row)

    def on_name_edited(self):
        row = self.playlist.currentRow()
        if row < 0:
            return
        item = self.playlist.item(row)
        name = self.name_edit.text().strip()
        item.setData(NAME_ROLE, name)
        item.setText(scene_label(item.data(NUMBER_ROLE), item.data(PATH_ROLE), item.data(AUDIO_PATH_ROLE), name))
        self.update_current_highlight()

    def on_selection_changed(self, row):
        if row < 0 or row >= self.playlist.count():
            self.name_edit.blockSignals(True)
            self.name_edit.clear()
            self.name_edit.setEnabled(False)
            self.name_edit.blockSignals(False)
            self.detail_primary_label.setText("(vide) \u2014 glisse un fichier ici")
            self.detail_secondary_label.setText("(vide) \u2014 glisse un fichier ici")
            for w in (self.btn_assign_primary, self.btn_clear_primary,
                      self.btn_assign_secondary, self.btn_clear_secondary,
                      self.detail_primary_label, self.detail_secondary_label,
                      self.auto_next_checkbox, self.btn_copy_scene, self.btn_paste_scene):
                w.setEnabled(False)
            for cb in (self.mute_primary_checkbox, self.mute_secondary_checkbox, self.auto_next_checkbox):
                cb.blockSignals(True)
                cb.setChecked(False)
                cb.blockSignals(False)
            for sl, lbl in ((self.volume_primary_slider, self.volume_primary_value_label),
                            (self.volume_secondary_slider, self.volume_secondary_value_label)):
                sl.blockSignals(True)
                sl.setValue(100)
                sl.blockSignals(False)
                lbl.setText("100%")
            self.update_current_highlight()
            return
        item = self.playlist.item(row)
        locked = item.data(NUMBER_ROLE) == LOCKED_SCENE_NUMBER

        self.name_edit.setEnabled(True)
        self.name_edit.blockSignals(True)
        self.name_edit.setText(item.data(NAME_ROLE) or "")
        self.name_edit.blockSignals(False)

        for w in (self.btn_assign_primary, self.btn_clear_primary,
                  self.btn_assign_secondary, self.btn_clear_secondary,
                  self.detail_primary_label, self.detail_secondary_label,
                  self.auto_next_checkbox):
            w.setEnabled(not locked and not self.show_locked)

        self.name_edit.setEnabled(not self.show_locked)
        self.btn_copy_scene.setEnabled(not self.show_locked)
        self.btn_paste_scene.setEnabled(not locked and not self.show_locked and self.clipboard_scene is not None)

        primary = item.data(PATH_ROLE)
        secondary = item.data(AUDIO_PATH_ROLE)
        self.detail_primary_label.setText(os.path.basename(primary) if primary else "(vide) \u2014 glisse un fichier ici")
        self.detail_secondary_label.setText(os.path.basename(secondary) if secondary else "(vide) \u2014 glisse un fichier ici")
        self.mute_primary_checkbox.blockSignals(True)
        self.mute_primary_checkbox.setChecked(bool(item.data(MUTE_PRIMARY_ROLE)))
        self.mute_primary_checkbox.blockSignals(False)
        self.mute_secondary_checkbox.blockSignals(True)
        self.mute_secondary_checkbox.setChecked(bool(item.data(MUTE_SECONDARY_ROLE)))
        self.mute_secondary_checkbox.blockSignals(False)
        self.auto_next_checkbox.blockSignals(True)
        self.auto_next_checkbox.setChecked(bool(item.data(AUTO_NEXT_ROLE)))
        self.auto_next_checkbox.blockSignals(False)

        vol_primary = item.data(VOLUME_PRIMARY_ROLE)
        vol_primary = 100 if vol_primary is None else vol_primary
        vol_secondary = item.data(VOLUME_SECONDARY_ROLE)
        vol_secondary = 100 if vol_secondary is None else vol_secondary
        self.volume_primary_slider.blockSignals(True)
        self.volume_primary_slider.setValue(vol_primary)
        self.volume_primary_slider.blockSignals(False)
        self.volume_primary_value_label.setText(f"{vol_primary}%")
        self.volume_secondary_slider.blockSignals(True)
        self.volume_secondary_slider.setValue(vol_secondary)
        self.volume_secondary_slider.blockSignals(False)
        self.volume_secondary_value_label.setText(f"{vol_secondary}%")
        self.update_current_highlight()

    def on_auto_next_toggled(self, checked):
        row = self.playlist.currentRow()
        if row < 0:
            return
        item = self.playlist.item(row)
        item.setData(AUTO_NEXT_ROLE, checked)

    def copy_selected_scene(self):
        row = self.playlist.currentRow()
        if row < 0:
            return
        item = self.playlist.item(row)
        self.clipboard_scene = {
            "name": item.data(NAME_ROLE),
            "path": item.data(PATH_ROLE),
            "audio_path": item.data(AUDIO_PATH_ROLE),
            "mute_primary": bool(item.data(MUTE_PRIMARY_ROLE)),
            "mute_secondary": bool(item.data(MUTE_SECONDARY_ROLE)),
            "volume_primary": item.data(VOLUME_PRIMARY_ROLE),
            "volume_secondary": item.data(VOLUME_SECONDARY_ROLE),
            "auto_next": bool(item.data(AUTO_NEXT_ROLE)),
        }
        source_label = item.data(NAME_ROLE) or f"scene {item.data(NUMBER_ROLE)}"
        self.clipboard_status_label.setText(f"Presse-papiers : {source_label}")
        self.on_selection_changed(self.playlist.currentRow())

    def paste_scene_to_selected(self):
        row = self.playlist.currentRow()
        if row < 0 or self.clipboard_scene is None:
            return
        if self._is_locked_row(row):
            QMessageBox.information(self, "Scene reservee", f"La scene {LOCKED_SCENE_NUMBER} ne peut pas recevoir de fichier.")
            return
        item = self.playlist.item(row)
        data = self.clipboard_scene
        item.setData(NAME_ROLE, data["name"])
        item.setData(PATH_ROLE, data["path"])
        item.setData(AUDIO_PATH_ROLE, data["audio_path"])
        item.setData(MUTE_PRIMARY_ROLE, data["mute_primary"])
        item.setData(MUTE_SECONDARY_ROLE, data["mute_secondary"])
        item.setData(VOLUME_PRIMARY_ROLE, data["volume_primary"])
        item.setData(VOLUME_SECONDARY_ROLE, data["volume_secondary"])
        item.setData(AUTO_NEXT_ROLE, data["auto_next"])
        item.setText(scene_label(item.data(NUMBER_ROLE), data["path"], data["audio_path"], data["name"]))
        self.on_selection_changed(row)

    def on_mute_primary_toggled(self, checked):
        row = self.playlist.currentRow()
        if row < 0:
            return
        item = self.playlist.item(row)
        item.setData(MUTE_PRIMARY_ROLE, checked)
        if row == self.current_index:
            self.program_player.audio_set_mute(checked)

    def on_mute_secondary_toggled(self, checked):
        row = self.playlist.currentRow()
        if row < 0:
            return
        item = self.playlist.item(row)
        item.setData(MUTE_SECONDARY_ROLE, checked)
        if row == self.current_index:
            self.program_audio_player.audio_set_mute(checked)

    def on_volume_primary_changed(self, value):
        self.volume_primary_value_label.setText(f"{value}%")
        row = self.playlist.currentRow()
        if row < 0:
            return
        item = self.playlist.item(row)
        item.setData(VOLUME_PRIMARY_ROLE, value)
        if row == self.current_index:
            self.program_player.audio_set_volume(value)

    def on_volume_secondary_changed(self, value):
        self.volume_secondary_value_label.setText(f"{value}%")
        row = self.playlist.currentRow()
        if row < 0:
            return
        item = self.playlist.item(row)
        item.setData(VOLUME_SECONDARY_ROLE, value)
        if row == self.current_index:
            self.program_audio_player.audio_set_volume(value)

    def toggle_current_primary_mute(self):
        if not (0 <= self.current_index < self.playlist.count()):
            return
        item = self.playlist.item(self.current_index)
        self.set_mute_primary(not bool(item.data(MUTE_PRIMARY_ROLE)))

    def toggle_current_secondary_mute(self):
        if not (0 <= self.current_index < self.playlist.count()):
            return
        item = self.playlist.item(self.current_index)
        self.set_mute_secondary(not bool(item.data(MUTE_SECONDARY_ROLE)))

    def adjust_current_primary_volume(self, delta):
        if not (0 <= self.current_index < self.playlist.count()):
            return
        item = self.playlist.item(self.current_index)
        current = item.data(VOLUME_PRIMARY_ROLE)
        current = 100 if current is None else current
        self.set_volume_primary(max(0, min(100, current + delta)))

    def adjust_current_secondary_volume(self, delta):
        if not (0 <= self.current_index < self.playlist.count()):
            return
        item = self.playlist.item(self.current_index)
        current = item.data(VOLUME_SECONDARY_ROLE)
        current = 100 if current is None else current
        self.set_volume_secondary(max(0, min(100, current + delta)))

    def set_mute_primary(self, muted):
        self.program_player.audio_set_mute(muted)
        if 0 <= self.current_index < self.playlist.count():
            item = self.playlist.item(self.current_index)
            item.setData(MUTE_PRIMARY_ROLE, muted)
            if self.playlist.currentRow() == self.current_index:
                self.mute_primary_checkbox.blockSignals(True)
                self.mute_primary_checkbox.setChecked(muted)
                self.mute_primary_checkbox.blockSignals(False)

    def set_mute_secondary(self, muted):
        self.program_audio_player.audio_set_mute(muted)
        if 0 <= self.current_index < self.playlist.count():
            item = self.playlist.item(self.current_index)
            item.setData(MUTE_SECONDARY_ROLE, muted)
            if self.playlist.currentRow() == self.current_index:
                self.mute_secondary_checkbox.blockSignals(True)
                self.mute_secondary_checkbox.setChecked(muted)
                self.mute_secondary_checkbox.blockSignals(False)

    def set_volume_primary(self, value):
        self.program_player.audio_set_volume(value)
        if 0 <= self.current_index < self.playlist.count():
            item = self.playlist.item(self.current_index)
            item.setData(VOLUME_PRIMARY_ROLE, value)
            if self.playlist.currentRow() == self.current_index:
                self.volume_primary_slider.blockSignals(True)
                self.volume_primary_slider.setValue(value)
                self.volume_primary_slider.blockSignals(False)
                self.volume_primary_value_label.setText(f"{value}%")

    def set_volume_secondary(self, value):
        self.program_audio_player.audio_set_volume(value)
        if 0 <= self.current_index < self.playlist.count():
            item = self.playlist.item(self.current_index)
            item.setData(VOLUME_SECONDARY_ROLE, value)
            if self.playlist.currentRow() == self.current_index:
                self.volume_secondary_slider.blockSignals(True)
                self.volume_secondary_slider.setValue(value)
                self.volume_secondary_slider.blockSignals(False)
                self.volume_secondary_value_label.setText(f"{value}%")

    # ------------------------------------------------------------------ #
    def keyPressEvent(self, event):
        self.handle_key(event)

    def handle_key(self, event):
        if event.key() == Qt.Key_Space:
            self.go_next()
        elif event.key() == Qt.Key_Backspace:
            self.go_previous()
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------ #
    def go_next(self):
        next_index = self.current_index + 1
        if next_index >= self.playlist.count():
            self.status_label.setText("Fin de la liste")
            return
        self.play_scene(next_index)

    def go_previous(self):
        prev_index = self.current_index - 1
        if prev_index < 0:
            return
        self.play_scene(prev_index)

    def goto_scene_number(self, number):
        for row in range(self.playlist.count()):
            item = self.playlist.item(row)
            if item.data(NUMBER_ROLE) == str(number):
                self.play_scene(row)
                return

    def play_scene(self, index):
        item = self.playlist.item(index)
        if item is None:
            return

        path = item.data(PATH_ROLE)
        audio_path = item.data(AUDIO_PATH_ROLE)

        if path:
            media_preview = self.vlc_instance.media_new(path)
            media_preview.add_option(f':file-caching={FILE_CACHING_MS}')
            # Apercu sans AUCUN audio : audio_set_mute() juste apres play()
            # est ignore sur certaines machines -> le son sortait deux fois
            # (apercu + programme), d'ou l'effet de reverb.
            media_preview.add_option(':no-audio')
            media_program = self.vlc_instance.media_new(path)
            media_program.add_option(f':file-caching={FILE_CACHING_MS}')
            self.preview_player.set_media(media_preview)
            self.program_player.set_media(media_program)
            self.preview_player.play()
            self.program_player.play()
            self.preview_player.audio_set_mute(True)
            self.program_player.audio_set_mute(bool(item.data(MUTE_PRIMARY_ROLE)))
            vol_primary = item.data(VOLUME_PRIMARY_ROLE)
            self.program_player.audio_set_volume(100 if vol_primary is None else vol_primary)
        else:
            self.preview_player.stop()
            self.program_player.stop()

        if audio_path:
            media_audio = self.vlc_instance.media_new(audio_path)
            media_audio.add_option(':no-video')
            media_audio.add_option(f':file-caching={FILE_CACHING_MS}')
            self.program_audio_player.set_media(media_audio)
            self.program_audio_player.play()
            self.program_audio_player.audio_set_mute(bool(item.data(MUTE_SECONDARY_ROLE)))
            vol_secondary = item.data(VOLUME_SECONDARY_ROLE)
            self.program_audio_player.audio_set_volume(100 if vol_secondary is None else vol_secondary)
        else:
            self.program_audio_player.stop()

        # Reapplique mute/volume une fois l'audio VLC reellement demarre
        # (sinon ignore sur certaines machines).
        QTimer.singleShot(300, lambda: self._apply_audio_state(index))
        QTimer.singleShot(1000, lambda: self._apply_audio_state(index))

        self.current_index = index
        self.playlist.setCurrentRow(index)
        if path or audio_path:
            self.status_label.setText(f"En cours : {item.text()}")
        else:
            self.status_label.setText(f"Scene {item.data(NUMBER_ROLE)} (vide)")
        self.update_current_highlight()
        self._prewarm_next_scene(index)
        self._schedule_blank_check(index, bool(path))

    def _apply_audio_state(self, index):
        if self.current_index != index:
            return
        item = self.playlist.item(index)
        if item is None:
            return
        self.preview_player.audio_set_mute(True)
        vp = item.data(VOLUME_PRIMARY_ROLE)
        vs = item.data(VOLUME_SECONDARY_ROLE)
        self.program_player.audio_set_mute(bool(item.data(MUTE_PRIMARY_ROLE)))
        self.program_player.audio_set_volume(100 if vp is None else vp)
        self.program_audio_player.audio_set_mute(bool(item.data(MUTE_SECONDARY_ROLE)))
        self.program_audio_player.audio_set_volume(100 if vs is None else vs)

    def _prewarm_next_scene(self, index):
        """Lit en arriere-plan les premiers Mo des fichiers de la PROCHAINE
        scene, pour les faire monter dans le cache disque du systeme et
        limiter les accrocs au demarrage quand on y accedera reellement."""
        next_index = index + 1
        if next_index >= self.playlist.count():
            return
        item = self.playlist.item(next_index)
        for path in (item.data(PATH_ROLE), item.data(AUDIO_PATH_ROLE)):
            if path:
                threading.Thread(target=self._prewarm_file, args=(path,), daemon=True).start()

    @staticmethod
    def _prewarm_file(path):
        try:
            with open(path, "rb") as f:
                f.read(PREWARM_BYTES)
        except OSError:
            pass

    def _blank_video_outputs(self):
        """Repeint en noir l'apercu et la sortie (efface la derniere image
        laissee par VLC)."""
        for frame in (self.preview_frame, self.output_window.video_frame):
            frame.repaint()

    def _blank_if_still_current(self, index, force):
        if self.current_index != index:
            return
        if force or self.program_player.has_vout() == 0:
            self._blank_video_outputs()

    def _schedule_blank_check(self, index, has_primary):
        if not has_primary:
            # Pas de media principal : plus d'image a afficher -> noir.
            self._blank_video_outputs()
            QTimer.singleShot(200, lambda: self._blank_if_still_current(index, True))
        else:
            # Media principal sans piste video (ex. mp3) -> noir aussi.
            for delay in (400, 1200):
                QTimer.singleShot(delay, lambda: self._blank_if_still_current(index, False))

    def stop_playback(self):
        self.preview_player.stop()
        self.program_player.stop()
        self.program_audio_player.stop()
        self.status_label.setText("Arrete")
        self._blank_video_outputs()
        QTimer.singleShot(200, self._blank_video_outputs)

    # ------------------------------------------------------------------ #
    def update_progress(self):
        if self.deck_window.isVisible():
            self.deck_window.refresh()  # suit mutes/renommages meme hors clic
        item = self.playlist.item(self.current_index) if 0 <= self.current_index < self.playlist.count() else None
        if item is not None and item.data(PATH_ROLE):
            player = self.program_player
        elif item is not None and item.data(AUDIO_PATH_ROLE):
            player = self.program_audio_player
        else:
            player = self.program_player

        length = player.get_length()
        time = player.get_time()
        if length and length > 0:
            self.progress_bar.setMaximum(length)
            self.progress_bar.setValue(max(0, time))
            self.time_label.setText(f"{format_ms(time)} / {format_ms(length)}")
        else:
            self.progress_bar.setValue(0)
            self.time_label.setText("00:00 / 00:00")

    # ------------------------------------------------------------------ #
    def save_show(self):
        if self.current_show_path:
            self._write_show(self.current_show_path)
        else:
            self.save_show_as()

    def save_show_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "Enregistrer le show", "", "Show JSON (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        self._write_show(path)

    def _write_show(self, path):
        data = {
            "scenes": [
                {
                    "number": self.playlist.item(i).data(NUMBER_ROLE),
                    "name": self.playlist.item(i).data(NAME_ROLE),
                    "path": self.playlist.item(i).data(PATH_ROLE),
                    "audio_path": self.playlist.item(i).data(AUDIO_PATH_ROLE),
                    "mute_primary": bool(self.playlist.item(i).data(MUTE_PRIMARY_ROLE)),
                    "mute_secondary": bool(self.playlist.item(i).data(MUTE_SECONDARY_ROLE)),
                    "volume_primary": self.playlist.item(i).data(VOLUME_PRIMARY_ROLE),
                    "volume_secondary": self.playlist.item(i).data(VOLUME_SECONDARY_ROLE),
                    "auto_next": bool(self.playlist.item(i).data(AUTO_NEXT_ROLE)),
                }
                for i in range(self.playlist.count())
            ]
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self.current_show_path = path
        self.setWindowTitle(f"Controle de scenes \u2014 {os.path.basename(path)}")

    def open_show(self):
        path, _ = QFileDialog.getOpenFileName(self, "Ouvrir un show", "", "Show JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            QMessageBox.warning(self, "Erreur", f"Impossible de lire ce fichier : {e}")
            return

        self.playlist.clear()
        for scene in data.get("scenes", []):
            item = QListWidgetItem()
            number = str(scene.get("number", ""))
            name = scene.get("name", "") or ""
            scene_path = scene.get("path", "") or ""
            audio_path = scene.get("audio_path", "") or ""
            mute_primary = bool(scene.get("mute_primary", False))
            mute_secondary = bool(scene.get("mute_secondary", False))
            volume_primary = scene.get("volume_primary", 100)
            volume_secondary = scene.get("volume_secondary", 100)
            auto_next = bool(scene.get("auto_next", False))
            item.setData(NUMBER_ROLE, number)
            item.setData(NAME_ROLE, name)
            item.setData(PATH_ROLE, scene_path)
            item.setData(AUDIO_PATH_ROLE, audio_path)
            item.setData(MUTE_PRIMARY_ROLE, mute_primary)
            item.setData(MUTE_SECONDARY_ROLE, mute_secondary)
            item.setData(VOLUME_PRIMARY_ROLE, volume_primary)
            item.setData(VOLUME_SECONDARY_ROLE, volume_secondary)
            item.setData(AUTO_NEXT_ROLE, auto_next)
            item.setText(scene_label(number, scene_path, audio_path, name))
            self.playlist.addItem(item)

        self.current_index = -1
        self.current_show_path = path
        self.status_label.setText("Aucune scene en cours")
        self.setWindowTitle(f"Controle de scenes \u2014 {os.path.basename(path)}")
        self.update_current_highlight()

    # ------------------------------------------------------------------ #
    def start_api_server(self):
        self.bridge = Bridge()
        self.bridge.window = self
        self.bridge.goto_number.connect(self.goto_scene_number)
        self.bridge.next_scene.connect(self.go_next)
        self.bridge.previous_scene.connect(self.go_previous)
        self.bridge.stop_scene.connect(self.stop_playback)
        self.bridge.mute_primary.connect(self.set_mute_primary)
        self.bridge.mute_secondary.connect(self.set_mute_secondary)
        self.bridge.volume_primary.connect(self.set_volume_primary)
        self.bridge.volume_secondary.connect(self.set_volume_secondary)

        handler_cls = make_http_handler(self.bridge)
        try:
            self.http_server = HTTPServer(("0.0.0.0", API_PORT), handler_cls)
        except OSError as e:
            self.api_label.setText(f"API HTTP indisponible sur le port {API_PORT} : {e}")
            self.http_server = None
            return

        self.http_thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
        self.http_thread.start()
        self.api_label.setText(
            f"API HTTP sur http://<ip-de-cette-machine>:{API_PORT}  "
            f"(/scene/<n>, /next, /previous, /stop, /status, /mute/../on|off, /volume/../0-100)"
        )

    # ------------------------------------------------------------------ #
    def closeEvent(self, event):
        self.timer.stop()
        if getattr(self, "http_server", None) is not None:
            self.http_server.shutdown()
        self.preview_player.stop()
        self.program_player.stop()
        self.program_audio_player.stop()
        self.preview_player.release()
        self.program_player.release()
        self.program_audio_player.release()
        self.vlc_instance.release()
        self.output_window.close()
        self.deck_window.close()
        super().closeEvent(event)


def main():
    # Linux/Wayland : VLC integre sa video via un identifiant de fenetre X11
    # (set_xwindow) et Wayland ne gere ni l'activation de fenetre ni cette
    # integration. On force donc Qt en mode X11 (XWayland), sauf si
    # l'utilisateur a deja choisi une plateforme.
    if platform.system() == "Linux" and "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    app = QApplication(sys.argv)
    control = ControlWindow()
    control.show()
    control.output_window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

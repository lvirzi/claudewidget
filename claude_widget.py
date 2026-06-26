#!/usr/bin/env python3
"""
Claude Widget — Windows desktop widget for Claude Code account status.
Reads usage data directly from ~/.claude/projects/ JSONL session files.
No API key required.
"""
import sys
import json
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

try:
    from PyQt6.QtWidgets import (
        QApplication, QWidget, QSystemTrayIcon, QMenu,
        QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
        QDialog, QFormLayout, QLineEdit, QComboBox, QFrame,
        QSpinBox,
    )
    from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QFileSystemWatcher
    from PyQt6.QtGui import QIcon, QPainter, QColor, QPixmap, QAction, QFont
except ImportError:
    print("Missing dependency. Run:  pip install PyQt6")
    sys.exit(1)

# ── Paths ─────────────────────────────────────────────────────────────────────
HOME         = Path.home()
CLAUDE_DIR   = HOME / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"
SETTINGS_FILE= CLAUDE_DIR / "settings.json"
WIDGET_DIR   = HOME / ".claude_widget"
CONFIG_FILE  = WIDGET_DIR / "config.json"

# ── Model catalogue ───────────────────────────────────────────────────────────
MODELS = {
    "claude-opus-4-8":           {"context": 200_000,   "input": 15.00, "output": 75.00},
    "claude-sonnet-4-6":         {"context": 200_000,   "input":  3.00, "output": 15.00},
    "claude-sonnet-4-6[1m]":     {"context": 1_000_000, "input":  3.00, "output": 15.00},
    "claude-haiku-4-5-20251001": {"context": 200_000,   "input":  0.80, "output":  4.00},
    "claude-fable-5":            {"context": 200_000,   "input":  3.00, "output": 15.00},
}

# Short aliases used by Claude Code in settings.json
_ALIASES = {
    "sonnet[1m]": "claude-sonnet-4-6[1m]",
    "sonnet":     "claude-sonnet-4-6",
    "opus":       "claude-opus-4-8",
    "haiku":      "claude-haiku-4-5-20251001",
    "fable":      "claude-fable-5",
}

def norm_model(m: str) -> str:
    return _ALIASES.get(m, m) if m else "claude-sonnet-4-6"

def model_info(mid: str) -> dict:
    if mid in MODELS:
        return MODELS[mid]
    for k, v in MODELS.items():
        base = k.split("[")[0]
        if mid.startswith(base):
            return v
    return {"context": 200_000, "input": 3.00, "output": 15.00}

def fmt_tok(n: int) -> str:
    if n >= 1_000_000: return f"{n/1_000_000:.2f}M"
    if n >= 1_000:     return f"{n/1_000:.1f}K"
    return str(n)

# ── Config ────────────────────────────────────────────────────────────────────
class Config:
    _D = {"always_on_top": True, "position": None, "refresh_interval": 30}

    def __init__(self):
        WIDGET_DIR.mkdir(parents=True, exist_ok=True)
        self.data = self._load()

    def _load(self) -> dict:
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, encoding="utf-8") as f:
                    return {**self._D, **json.load(f)}
            except Exception:
                pass
        return dict(self._D)

    def save(self):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)

# ── Local JSONL reader ────────────────────────────────────────────────────────
class LocalReader:
    """Reads Claude Code session data from ~/.claude/projects/**/*.jsonl"""

    def _iter_jsonl(self, path: Path):
        """Yield parsed JSON objects from a JSONL file, skipping bad lines."""
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
        except Exception:
            return

    def _all_session_files(self):
        if not PROJECTS_DIR.exists():
            return
        for proj in PROJECTS_DIR.iterdir():
            if not proj.is_dir():
                continue
            for jl in proj.glob("*.jsonl"):
                yield jl

    def get_settings_model(self) -> str:
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                return norm_model(json.load(f).get("model", ""))
        except Exception:
            return "claude-sonnet-4-6"

    def read_usage(self, days: int = 31) -> dict:
        """
        Returns aggregated usage per day:
          { "2026-06-26": { "model_id": {inp, out, cache_create, cache_read} } }
        Deduplicates by message.id to avoid counting Claude Code's duplicate writes.
        """
        cutoff = date.today() - timedelta(days=days)
        result  = {}
        seen    = set()

        for jl in self._all_session_files():
            for obj in self._iter_jsonl(jl):
                if obj.get("type") != "assistant":
                    continue
                msg = obj.get("message") or {}
                mid = msg.get("id")
                if not mid or mid in seen:
                    continue
                usage = msg.get("usage")
                if not usage:
                    continue

                # Parse timestamp
                ts_str = obj.get("timestamp", "")
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    d  = ts.astimezone(timezone.utc).date()
                except Exception:
                    continue

                if d < cutoff:
                    continue

                seen.add(mid)
                model = norm_model(msg.get("model", ""))
                day   = str(d)

                if day not in result:
                    result[day] = {}
                if model not in result[day]:
                    result[day][model] = {"inp": 0, "out": 0, "cc": 0, "cr": 0}
                r = result[day][model]
                r["inp"] += usage.get("input_tokens", 0)
                r["out"] += usage.get("output_tokens", 0)
                r["cc"]  += usage.get("cache_creation_input_tokens", 0)
                r["cr"]  += usage.get("cache_read_input_tokens", 0)

        return result

    def read_active_session(self) -> dict:
        """
        Returns info from the most recently updated session file:
          model, context_tokens (proxy), last_output, last_ts
        """
        latest_file  = None
        latest_mtime = 0.0

        for jl in self._all_session_files():
            try:
                mt = jl.stat().st_mtime
                if mt > latest_mtime:
                    latest_mtime = mt
                    latest_file  = jl
            except Exception:
                continue

        if not latest_file:
            return {}

        last_usage = None
        last_model = ""
        last_ts    = ""
        seen       = set()

        for obj in self._iter_jsonl(latest_file):
            if obj.get("type") != "assistant":
                continue
            msg = obj.get("message") or {}
            mid = msg.get("id")
            if not mid or mid in seen:
                continue
            seen.add(mid)
            u = msg.get("usage")
            if u:
                last_usage = u
                last_model = msg.get("model", "")
                last_ts    = obj.get("timestamp", "")

        if not last_usage:
            return {}

        context = (
            last_usage.get("cache_read_input_tokens", 0) +
            last_usage.get("cache_creation_input_tokens", 0) +
            last_usage.get("input_tokens", 0)
        )
        return {
            "model":   norm_model(last_model),
            "context": context,
            "output":  last_usage.get("output_tokens", 0),
            "ts":      last_ts,
        }

    def read_sessions(self) -> list:
        """
        Returns list of Claude Code sessions from ~/.claude/sessions/*.json,
        sorted by: alive first, then most recently updated.
        Each dict has: pid, alive, status, waitingFor, project, updatedAt.
        """
        sessions_dir = CLAUDE_DIR / "sessions"
        result = []
        if not sessions_dir.exists():
            return result
        for f in sessions_dir.glob("*.json"):
            try:
                with open(f, encoding="utf-8") as fh:
                    s = json.load(fh)
                pid   = s.get("pid", 0)
                alive = _is_pid_running(pid)
                cwd   = s.get("cwd", "")
                result.append({
                    "pid":        pid,
                    "alive":      alive,
                    "status":     s.get("status", ""),
                    "waitingFor": s.get("waitingFor", ""),
                    "project":    Path(cwd).name if cwd else "?",
                    "updatedAt":  s.get("updatedAt", 0),
                })
            except Exception:
                continue
        result.sort(key=lambda x: (not x["alive"], -x["updatedAt"]))
        return result


def _is_pid_running(pid: int) -> bool:
    """Check if a Windows process is alive without importing psutil."""
    if not pid:
        return False
    try:
        import ctypes
        PROCESS_QUERY_INFORMATION = 0x0400
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
    except Exception:
        pass
    return False


def calc_cost(model: str, inp: int, out: int, cc: int, cr: int) -> float:
    info = model_info(model)
    p_in  = info["input"]
    p_out = info["output"]
    return (
        inp * p_in        / 1_000_000 +
        out * p_out       / 1_000_000 +
        cc  * p_in * 1.25 / 1_000_000 +
        cr  * p_in * 0.10 / 1_000_000
    )

def aggregate_day(day_data: dict) -> tuple:
    """Returns (inp, out, cc, cr, cost) summed across all models for one day."""
    inp = out = cc = cr = 0
    cost = 0.0
    for model, u in day_data.items():
        inp  += u["inp"]; out += u["out"]
        cc   += u["cc"];  cr  += u["cr"]
        cost += calc_cost(model, u["inp"], u["out"], u["cc"], u["cr"])
    return inp, out, cc, cr, cost

# ── Background worker ─────────────────────────────────────────────────────────
class RefreshWorker(QThread):
    done = pyqtSignal(dict)

    def __init__(self, reader: LocalReader):
        super().__init__()
        self.reader = reader

    def run(self):
        try:
            usage    = self.reader.read_usage(days=31)
            active   = self.reader.read_active_session()
            model    = self.reader.get_settings_model()
            sessions = self.reader.read_sessions()
            self.done.emit({"usage": usage, "active": active,
                            "model": model, "sessions": sessions})
        except Exception as e:
            self.done.emit({"error": str(e)})

# ── Colour palette & stylesheet ───────────────────────────────────────────────
C_BG     = "#1a1b2e"
C_BORDER = "#0f3460"
C_ACCENT = "#7c83fd"
C_PRI    = "#e2e8f0"
C_SEC    = "#8892a4"
C_MONO   = "#a8d5a2"
C_CACHE  = "#f6c90e"
C_CARD   = "#16213e"

# Session status colours
C_BUSY  = "#4ade80"   # green  — actively processing
C_WAIT  = "#facc15"   # yellow — waiting for user input
C_PERM  = "#fb923c"   # orange — waiting for permission
C_DEAD  = "#475569"   # slate  — process no longer running

QSS = f"""
* {{
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 11px;
    color: {C_PRI};
}}
#root {{
    background: {C_BG};
    border: 1px solid {C_BORDER};
    border-radius: 12px;
}}
#hdr  {{ font-size: 13px; font-weight: 700; }}
#sec  {{ font-size: 9px; font-weight: 600; color: {C_ACCENT}; letter-spacing: 1px; }}
#key  {{ color: {C_SEC}; }}
#val  {{ color: {C_MONO}; font-family: 'Consolas', monospace; }}
#vcache {{ color: {C_CACHE}; font-family: 'Consolas', monospace; }}
#vcost  {{ color: #ff9966; font-family: 'Consolas', monospace; }}
#status {{ font-size: 10px; color: {C_SEC}; }}
#sess   {{ font-size: 11px; font-family: 'Consolas', monospace; }}
#sep    {{ background: {C_BORDER}; max-height: 1px; border: none; }}
QLabel  {{ background: transparent; }}
QPushButton#ib {{
    background: transparent; border: none;
    color: {C_SEC}; font-size: 14px;
    padding: 2px 3px; border-radius: 4px;
    min-width: 22px; min-height: 22px;
}}
QPushButton#ib:hover {{ background: {C_CARD}; color: {C_PRI}; }}
QDialog {{ background: {C_BG}; color: {C_PRI}; }}
QLineEdit, QComboBox, QSpinBox {{
    background: {C_CARD}; border: 1px solid {C_BORDER};
    border-radius: 4px; color: {C_PRI}; padding: 4px 8px;
}}
QPushButton:not(#ib) {{
    background: {C_ACCENT}; border: none; border-radius: 4px;
    color: white; padding: 6px 14px; font-weight: 600;
}}
QPushButton:not(#ib):hover {{ background: #9099ff; }}
QPushButton:not(#ib)[flat="true"] {{
    background: transparent;
    color: {C_PRI};
    border: 1px solid {C_BORDER};
}}
QPushButton:not(#ib)[flat="true"]:hover {{
    background: {C_CARD};
}}
"""

# ── UI helpers ────────────────────────────────────────────────────────────────
def sep() -> QFrame:
    f = QFrame(); f.setObjectName("sep")
    f.setFrameShape(QFrame.Shape.HLine); return f

def sec(text: str) -> QLabel:
    l = QLabel(text); l.setObjectName("sec"); return l

class Row:
    def __init__(self, key: str, w: int = 82, color_id: str = "val"):
        self.layout = QHBoxLayout()
        self.layout.setSpacing(4)
        self.layout.setContentsMargins(0, 0, 0, 0)
        kl = QLabel(key + ":"); kl.setObjectName("key"); kl.setFixedWidth(w)
        self.vl = QLabel("—"); self.vl.setObjectName(color_id)
        self.layout.addWidget(kl)
        self.layout.addWidget(self.vl)
        self.layout.addStretch()

    def set(self, t: str): self.vl.setText(t)

# ── Settings dialog ───────────────────────────────────────────────────────────
class SettingsDialog(QDialog):
    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Claude Widget — Settings")
        self.setMinimumWidth(360)
        self.setStyleSheet(QSS)
        fl = QFormLayout(self)
        fl.setSpacing(10); fl.setContentsMargins(16, 16, 16, 16)

        self.interval = QSpinBox()
        self.interval.setRange(10, 300)
        self.interval.setValue(cfg.data.get("refresh_interval", 30))
        self.interval.setSuffix(" s")
        fl.addRow("Refresh every:", self.interval)

        note = QLabel(
            "Usage is read directly from\n"
            "~/.claude/projects/ — no API key needed."
        )
        note.setObjectName("key"); note.setWordWrap(True)
        fl.addRow("", note)

        row = QHBoxLayout()
        ok  = QPushButton("Save"); ok.clicked.connect(self._save)
        can = QPushButton("Cancel"); can.setProperty("flat", "true")
        can.clicked.connect(self.reject)
        row.addWidget(ok); row.addWidget(can)
        fl.addRow(row)

    def _save(self):
        self.cfg.data["refresh_interval"] = self.interval.value()
        self.cfg.save(); self.accept()

# ── Main overlay window ───────────────────────────────────────────────────────
class WidgetWindow(QWidget):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg     = cfg
        self.reader  = LocalReader()
        self._drag   = None
        self._pinned = cfg.data.get("always_on_top", True)
        self._worker = None

        self._build_ui()
        self._apply_flags(show=False)
        self._restore_pos()
        self._setup_timer()
        self._setup_watcher()
        self.refresh()

    # ── Build UI ───────────────────────────────────────────────────────
    def _build_ui(self):
        self.setWindowTitle("Claude Widget")
        self.setFixedWidth(300)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.root = QFrame(self); self.root.setObjectName("root")
        v = QVBoxLayout(self.root)
        v.setSpacing(5); v.setContentsMargins(14, 10, 14, 12)

        # Header
        hdr = QHBoxLayout()
        t = QLabel("● Claude Widget"); t.setObjectName("hdr")
        self.pin_btn = self._ib("📌", "Toggle always-on-top", self._toggle_pin)
        cfg_btn      = self._ib("⚙",  "Settings",             self._open_settings)
        hide_btn     = self._ib("✕",  "Minimise to tray",     self.hide)
        hdr.addWidget(t); hdr.addStretch()
        for b in (self.pin_btn, cfg_btn, hide_btn):
            hdr.addWidget(b)
        v.addLayout(hdr); v.addWidget(sep())

        # MODEL
        v.addWidget(sec("MODEL"))
        self.r_model   = Row("Name",    82)
        self.r_maxctx  = Row("Max ctx", 82)
        v.addLayout(self.r_model.layout)
        v.addLayout(self.r_maxctx.layout)
        v.addWidget(sep())

        # SESSIONS — dynamic rows (up to 4 slots, pre-allocated)
        v.addWidget(sec("SESSIONS"))
        self._sess_labels = []
        for _ in range(4):
            lbl = QLabel()
            lbl.setObjectName("sess")
            lbl.hide()
            v.addWidget(lbl)
            self._sess_labels.append(lbl)
        self._sess_none = QLabel("  no active sessions")
        self._sess_none.setObjectName("key")
        v.addWidget(self._sess_none)
        v.addWidget(sep())

        # ACTIVE SESSION
        v.addWidget(sec("ACTIVE SESSION"))
        self.r_ctx   = Row("Context", 82)
        self.r_last  = Row("Last out", 82)
        self.r_when  = Row("Updated",  82)
        v.addLayout(self.r_ctx.layout)
        v.addLayout(self.r_last.layout)
        v.addLayout(self.r_when.layout)
        v.addWidget(sep())

        # TODAY
        v.addWidget(sec("TODAY"))
        self.r_td_in    = Row("Input",   82)
        self.r_td_out   = Row("Output",  82)
        self.r_td_cache = Row("Cache↓",  82, "vcache")
        self.r_td_cost  = Row("Cost~",   82, "vcost")
        v.addLayout(self.r_td_in.layout)
        v.addLayout(self.r_td_out.layout)
        v.addLayout(self.r_td_cache.layout)
        v.addLayout(self.r_td_cost.layout)
        v.addWidget(sep())

        # THIS MONTH
        v.addWidget(sec("THIS MONTH"))
        self.r_mo_tok  = Row("Tokens",  82)
        self.r_mo_cost = Row("Cost~",   82, "vcost")
        v.addLayout(self.r_mo_tok.layout)
        v.addLayout(self.r_mo_cost.layout)
        v.addWidget(sep())

        # Status bar
        sr = QHBoxLayout()
        self.status = QLabel("⏳ Starting…"); self.status.setObjectName("status")
        rfr = self._ib("↻", "Refresh now", self.refresh)
        sr.addWidget(self.status); sr.addStretch(); sr.addWidget(rfr)
        v.addLayout(sr)

        self.root.adjustSize(); self.adjustSize()
        self.setStyleSheet(QSS)
        self._update_pin_style()

    def _ib(self, text, tip, slot):
        b = QPushButton(text); b.setObjectName("ib")
        b.setFixedSize(22, 22); b.setToolTip(tip)
        b.clicked.connect(slot); return b

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.root.setGeometry(0, 0, self.width(), self.height())

    # ── Position / flags ───────────────────────────────────────────────
    def _restore_pos(self):
        p = self.cfg.data.get("position")
        if p:
            self.move(p[0], p[1])
        else:
            scr = QApplication.primaryScreen().availableGeometry()
            self.move(scr.right() - 320, scr.top() + 80)

    def _apply_flags(self, show: bool = True):
        visible = self.isVisible()
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self._pinned:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        if show or visible:
            self.show()

    def _update_pin_style(self):
        if self._pinned:
            self.pin_btn.setStyleSheet(
                "background: white; border-radius: 4px;"
            )
            self.pin_btn.setToolTip("Unpin (always-on-top ON)")
        else:
            self.pin_btn.setStyleSheet(
                f"background: transparent; border-radius: 4px;"
            )
            self.pin_btn.setToolTip("Pin (always-on-top OFF)")

    # ── Timer & file watcher ───────────────────────────────────────────
    def _setup_timer(self):
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(self.cfg.data.get("refresh_interval", 30) * 1000)

    def _setup_watcher(self):
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(self._on_fs_change)
        self.watcher.fileChanged.connect(self._on_fs_change)

        # Watch the projects directory for new session files
        if PROJECTS_DIR.exists():
            self.watcher.addPath(str(PROJECTS_DIR))
            for proj in PROJECTS_DIR.iterdir():
                if proj.is_dir():
                    self.watcher.addPath(str(proj))
                    for jl in proj.glob("*.jsonl"):
                        self.watcher.addPath(str(jl))

        # Debounce rapid file changes (Claude Code writes many lines quickly)
        self._fs_debounce = QTimer(self)
        self._fs_debounce.setSingleShot(True)
        self._fs_debounce.setInterval(2000)  # wait 2s after last change
        self._fs_debounce.timeout.connect(self.refresh)

    def _on_fs_change(self, path: str):
        # Re-watch new JSONL files that appear
        p = Path(path)
        if p.is_dir():
            for jl in p.glob("*.jsonl"):
                sp = str(jl)
                if sp not in self.watcher.files():
                    self.watcher.addPath(sp)
        self._fs_debounce.start()

    # ── Data refresh ───────────────────────────────────────────────────
    def refresh(self):
        if self._worker and self._worker.isRunning():
            return
        self.status.setText("⟳  Reading sessions…")
        self._worker = RefreshWorker(self.reader)
        self._worker.done.connect(self._on_data)
        self._worker.start()

    def _render_sessions(self, sessions: list):
        alive = [s for s in sessions if s["alive"]]
        dead  = [s for s in sessions if not s["alive"]]
        shown = (alive + dead)[:4]

        if not shown:
            self._sess_none.show()
            for lbl in self._sess_labels:
                lbl.hide()
            return

        self._sess_none.hide()
        for i, lbl in enumerate(self._sess_labels):
            if i < len(shown):
                s = shown[i]
                dot, color, tag = self._sess_fmt(s)
                project = s["project"][:18].ljust(18)
                lbl.setText(f"{dot}  {project}  {tag}")
                lbl.setStyleSheet(f"color: {color};")
                lbl.show()
            else:
                lbl.hide()

        if len(alive) + len(dead) > 4:
            extra = len(alive) + len(dead) - 4
            self._sess_labels[3].setText(f"   … and {extra} more")
            self._sess_labels[3].setStyleSheet(f"color: {C_SEC};")
            self._sess_labels[3].show()

    @staticmethod
    def _sess_fmt(s: dict) -> tuple:
        """Returns (dot_char, colour, tag_text) for a session dict."""
        if not s["alive"]:
            return "○", C_DEAD, "closed"
        status = s.get("status", "")
        wf     = s.get("waitingFor", "") or ""
        if status == "busy":
            return "●", C_BUSY, "busy"
        if "permission" in wf.lower():
            return "◉", C_PERM, "waiting · permission"
        if status == "waiting":
            return "◑", C_WAIT, "waiting · input"
        return "◌", C_SEC, status or "idle"

    def _on_data(self, data: dict):
        if "error" in data:
            self.status.setText(f"✗  {data['error']}")
            return

        usage    = data["usage"]
        active   = data["active"]
        model    = data.get("model") or "claude-sonnet-4-6"
        info     = model_info(model)
        sessions = data.get("sessions", [])

        # ── MODEL ──────────────────────────────────────────────────────
        self.r_model.set(model)
        self.r_maxctx.set(fmt_tok(info["context"]) + " tokens")

        # ── SESSIONS ───────────────────────────────────────────────────
        self._render_sessions(sessions)

        # ── ACTIVE SESSION ─────────────────────────────────────────────
        if active:
            ctx     = active["context"]
            max_ctx = model_info(active["model"])["context"]
            pct     = ctx / max_ctx * 100 if max_ctx else 0
            bar     = "▓" * int(pct / 10) + "░" * (10 - int(pct / 10))
            self.r_ctx.set(f"{fmt_tok(ctx)}  {bar}  {pct:.0f}%")
            self.r_last.set(fmt_tok(active["output"]) + " tokens")
            try:
                ts = datetime.fromisoformat(
                    active["ts"].replace("Z", "+00:00")
                ).astimezone()
                self.r_when.set(ts.strftime("%H:%M:%S"))
            except Exception:
                self.r_when.set("—")
        else:
            self.r_ctx.set("—"); self.r_last.set("—"); self.r_when.set("—")

        # ── TODAY ──────────────────────────────────────────────────────
        today_key = str(date.today())
        if today_key in usage:
            inp, out, cc, cr, cost = aggregate_day(usage[today_key])
            self.r_td_in.set(fmt_tok(inp))
            self.r_td_out.set(fmt_tok(out))
            self.r_td_cache.set(f"{fmt_tok(cr)}  (saved ${cr * model_info(model)['input'] * 0.90 / 1_000_000:.3f})")
            self.r_td_cost.set(f"${cost:.4f}")
        else:
            self.r_td_in.set("0"); self.r_td_out.set("0")
            self.r_td_cache.set("0"); self.r_td_cost.set("$0.0000")

        # ── THIS MONTH ─────────────────────────────────────────────────
        month_pfx = today_key[:7]
        mo_inp = mo_out = mo_cc = mo_cr = 0
        mo_cost = 0.0
        for day, day_data in usage.items():
            if day.startswith(month_pfx):
                i, o, c, r, cst = aggregate_day(day_data)
                mo_inp += i; mo_out += o; mo_cc += c; mo_cr += r
                mo_cost += cst
        self.r_mo_tok.set(fmt_tok(mo_inp + mo_out + mo_cc))
        self.r_mo_cost.set(f"${mo_cost:.2f}")

        now = datetime.now().strftime("%H:%M:%S")
        self.status.setText(f"✓  {now}")

    # ── Actions ────────────────────────────────────────────────────────
    def _toggle_pin(self):
        self._pinned = not self._pinned
        self.cfg.data["always_on_top"] = self._pinned
        self.cfg.save()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self._pinned)
        self.show()
        self._update_pin_style()

    def _open_settings(self):
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.timer.setInterval(self.cfg.data["refresh_interval"] * 1000)

    # ── Drag ───────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag and e.buttons() == Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = None
            p = self.pos()
            self.cfg.data["position"] = [p.x(), p.y()]
            self.cfg.save()

    def closeEvent(self, e):
        e.ignore(); self.hide()

# ── Application ───────────────────────────────────────────────────────────────
class App:
    def __init__(self):
        self.qt = QApplication(sys.argv)
        self.qt.setQuitOnLastWindowClosed(False)
        self.qt.setApplicationName("Claude Widget")

        self.cfg = Config()
        self.win = WidgetWindow(self.cfg)
        self._setup_tray()
        self.win._apply_flags(show=True)

    def _make_icon(self) -> QIcon:
        pm = QPixmap(32, 32)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(C_ACCENT)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(1, 1, 30, 30)
        p.setPen(QColor("white"))
        f = QFont("Arial", 15, QFont.Weight.Bold); p.setFont(f)
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "C")
        p.end()
        return QIcon(pm)

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self._make_icon(), self.qt)
        self.tray.setToolTip("Claude Widget")
        menu = QMenu()
        for label, slot in [
            ("Show / Hide",  self._toggle),
            ("Refresh",      self.win.refresh),
            (None, None),
            ("Settings…",    self.win._open_settings),
            (None, None),
            ("Quit",         self.qt.quit),
        ]:
            if label is None:
                menu.addSeparator()
            else:
                a = QAction(label, self.qt); a.triggered.connect(slot)
                menu.addAction(a)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_click)
        self.tray.show()

    def _toggle(self):
        if self.win.isVisible():
            self.win.hide()
        else:
            self.win.show(); self.win.raise_(); self.win.activateWindow()

    def _tray_click(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle()

    def run(self) -> int:
        return self.qt.exec()


if __name__ == "__main__":
    sys.exit(App().run())

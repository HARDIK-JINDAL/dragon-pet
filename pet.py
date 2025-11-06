

import sys, os, glob, io, math, random, time, traceback
from PIL import Image, ImageOps
from PyQt5 import QtCore, QtGui, QtWidgets

# -------- CONFIG (tweak to taste) ----------
TICK_MS = 25            # main timer tick (ms)
MAX_SPEED = 3.0         # max pixels per tick (top speed)
WALK_SPEED = 0.6        # nominal movement speed (pixels per tick)
ACCEL = 0.15            # how fast pet accelerates to WALK_SPEED (higher = snappier)
BOB_AMPLITUDE = 6       # bob amplitude (px)
BOUNCE_HEIGHT = 140     # bounce height (px)
BOUNCE_MS = 650         # bounce duration (ms)
SCALE = 0.90            # scale of GIF frames (0..1)
RAISE_PET = -120        # vertical offset relative to taskbar center (negative lifts up)
PAUSE_MIN = 0.18        # minimum random pause when reaching target (seconds)
PAUSE_MAX = 0.95        # maximum random pause when reaching target (seconds)
EDGE_MARGIN = 8         # keep this many px from screen edge
# --------------------------------------------

def find_first_gif():
    prefer = os.path.join(os.getcwd(), "dino.gif")
    if os.path.exists(prefer): return prefer
    gifs = sorted(glob.glob(os.path.join(os.getcwd(), "*.gif")))
    return gifs[0] if gifs else None

def load_gif_frames(path):
    im = Image.open(path)
    frames = []
    durations = []
    try:
        i = 0
        while True:
            im.seek(i)
            frame = im.convert("RGBA")
            duration = im.info.get("duration", 100)
            frames.append(frame.copy())
            durations.append(duration)
            i += 1
    except EOFError:
        pass
    if not frames:
        raise RuntimeError("No frames found in GIF")
    return frames, durations

def make_gif_bytes_from_frames(frames, durations):
    bio = io.BytesIO()
    p_frames = [f.convert("RGBA") for f in frames]
    p_frames[0].save(
        bio,
        format="GIF",
        save_all=True,
        append_images=p_frames[1:],
        loop=0,
        duration=durations,
        disposal=2
    )
    return bio.getvalue()

def scale_frames(frames, scale):
    if scale == 1.0: return frames
    out = []
    for f in frames:
        w, h = f.size
        tw = max(4, int(w * scale))
        th = max(4, int(h * scale))
        out.append(f.resize((tw, th), resample=Image.NEAREST))
    return out

def build_right_left_gifs(path, scale=SCALE):
    frames, durations = load_gif_frames(path)
    frames = scale_frames(frames, scale)
    right_bytes = make_gif_bytes_from_frames(frames, durations)
    left_frames = [ImageOps.mirror(f) for f in frames]
    left_bytes = make_gif_bytes_from_frames(left_frames, durations)
    tw, th = frames[0].size
    return right_bytes, left_bytes, (tw, th)

class SmoothGifPet(QtWidgets.QWidget):
    def __init__(self, right_bytes, left_bytes, size_tuple, raise_pet=RAISE_PET):
        # Use top-level window flags so it remains visible when switching apps
        flags = QtCore.Qt.Window | QtCore.Qt.FramelessWindowHint | QtCore.Qt.WindowStaysOnTopHint
        super().__init__(flags=flags)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
        try:
            self.setWindowFlag(QtCore.Qt.WindowDoesNotAcceptFocus, True)
        except Exception:
            pass

        self.raise_pet = raise_pet
        self.width_px, self.height_px = size_tuple
        self.label = QtWidgets.QLabel(self)
        self.label.setFixedSize(self.width_px, self.height_px)
        self.setFixedSize(self.width_px, self.height_px)

        # QMovies from bytes (QBuffer)
        self.right_buf = QtCore.QBuffer(); self.right_ba = QtCore.QByteArray(right_bytes)
        self.right_buf.setData(self.right_ba); self.right_buf.open(QtCore.QIODevice.ReadOnly)
        self.movie_right = QtGui.QMovie(self.right_buf, b'gif', self)

        self.left_buf = QtCore.QBuffer(); self.left_ba = QtCore.QByteArray(left_bytes)
        self.left_buf.setData(self.left_ba); self.left_buf.open(QtCore.QIODevice.ReadOnly)
        self.movie_left = QtGui.QMovie(self.left_buf, b'gif', self)

        # position and movement floats
        self.fx = 0.0
        self.vx = 0.0
        self.target_x = None
        self.wait_until = 0.0  # timestamp while paused
        self.dir = 1 if random.random() < 0.5 else -1

        # set initial movie
        self.set_movie(self.movie_right if self.dir > 0 else self.movie_left)

        self.phase = random.random() * 10.0
        self.bouncing = False

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(TICK_MS)

        QtCore.QTimer.singleShot(0, self._initial_place)
        self.label.mousePressEvent = self._on_click

    def set_movie(self, movie):
        try:
            movie.jumpToFrame(0)
            self.label.setMovie(movie)
            movie.start()
        except Exception:
            pass

    def _initial_place(self):
        screen = QtWidgets.QApplication.primaryScreen()
        full = screen.geometry()
        avail = screen.availableGeometry()
        taskbar_h = full.height() - avail.height()

        # choose random X
        start_x = random.randint(EDGE_MARGIN, max(EDGE_MARGIN, full.width() - self.width() - EDGE_MARGIN))
        self.fx = float(start_x)
        self.move(int(self.fx), self._y_base())
        self.show()
        # choose first target
        self._pick_new_target(initial=True)

    def _y_base(self):
        screen = QtWidgets.QApplication.primaryScreen()
        full = screen.geometry()
        avail = screen.availableGeometry()
        taskbar_h = full.height() - avail.height()
        return avail.bottom() + max(0, (taskbar_h - self.height()) // 2) + self.raise_pet

    def _pick_new_target(self, initial=False):
        full = QtWidgets.QApplication.primaryScreen().geometry()
        min_x = EDGE_MARGIN
        max_x = max(EDGE_MARGIN, full.width() - self.width() - EDGE_MARGIN)
        if initial:
            # pick target roughly across screen
            self.target_x = random.uniform(min_x, max_x)
            self.dir = 1 if self.target_x >= self.fx else -1
        else:
            # usually pick a target a short distance away in a direction (prevents long freezes)
            if random.random() < 0.07:
                # 7% chance to pick far-away target to look purposeful
                self.target_x = random.uniform(min_x, max_x)
            else:
                # prefer same direction but slight variation
                if self.dir >= 0:
                    low = min(self.fx + 40, max_x)
                    high = min(self.fx + 200, max_x)
                    if low >= high: low, high = min_x, max_x
                    self.target_x = random.uniform(low, high)
                else:
                    low = max(self.fx - 200, min_x)
                    high = max(self.fx - 40, min_x)
                    if low >= high: low, high = min_x, max_x
                    self.target_x = random.uniform(low, high)
            # set target within bounds
            self.target_x = max(min_x, min(max_x, self.target_x))
            self.dir = 1 if self.target_x >= self.fx else -1
        # if direction changed, swap movie
        if self.dir > 0:
            self.set_movie(self.movie_right)
        else:
            self.set_movie(self.movie_left)

    def _tick(self):
        now = time.time()
        # if currently paused
        if now < self.wait_until:
            # still bobbing, keep Y updated
            y = self._y_base() + math.sin(self.phase) * BOB_AMPLITUDE
            self.move(int(self.fx), int(y))
            self.phase += 0.10
            return

        screen = QtWidgets.QApplication.primaryScreen()
        full = screen.geometry()
        min_x = EDGE_MARGIN
        max_x = max(EDGE_MARGIN, full.width() - self.width() - EDGE_MARGIN)

        # pick a target if none
        if self.target_x is None:
            self._pick_new_target()

        # compute desired velocity toward target (smooth accel)
        dx = self.target_x - self.fx
        dist = abs(dx)
        desired_dir = 1 if dx >= 0 else -1
        # if very close to target: pause shortly then pick new
        if dist <= 2.0:
            # short pause before new target
            pause = random.uniform(PAUSE_MIN, PAUSE_MAX)
            self.wait_until = now + pause
            # after pause pick target on opposite side or some random offset
            # flip direction 50% or pick new target across screen
            if random.random() < 0.33:
                # pick opposite side target to look lively
                self.target_x = max_x if self.fx < (max_x/2) else min_x
            else:
                # pick a new target nearby
                self.target_x = random.uniform(min_x, max_x)
            self.dir = 1 if self.target_x >= self.fx else -1
            if self.dir > 0: self.set_movie(self.movie_right)
            else: self.set_movie(self.movie_left)
            return

        # accelerate vx towards desired speed (based on dist)
        target_speed = min(WALK_SPEED + (dist / 50.0), MAX_SPEED) * (1 if dx > 0 else -1)
        # simple accel
        self.vx += (target_speed - self.vx) * min(1.0, ACCEL)
        # move
        self.fx += self.vx
        # clamp
        if self.fx < min_x:
            self.fx = min_x
            self.target_x = random.uniform(min_x, max_x)
            self.dir = 1
            self.set_movie(self.movie_right)
        elif self.fx > max_x:
            self.fx = max_x
            self.target_x = random.uniform(min_x, max_x)
            self.dir = -1
            self.set_movie(self.movie_left)

        # bob and move widget
        self.phase += 0.10
        y = self._y_base() + math.sin(self.phase) * BOB_AMPLITUDE
        self.move(int(self.fx), int(y))

    def _on_click(self, ev):
        if ev.button() != QtCore.Qt.LeftButton: return
        if self.bouncing: return
        self._start_bounce()

    def _start_bounce(self):
        self.bouncing = True
        start = self.pos()
        up = QtCore.QPoint(start.x(), max(0, start.y() - BOUNCE_HEIGHT))
        anim = QtCore.QPropertyAnimation(self, b"pos", self)
        anim.setDuration(BOUNCE_MS)
        anim.setKeyValueAt(0.0, start)
        anim.setKeyValueAt(0.35, up)
        anim.setKeyValueAt(1.0, start)
        anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        def on_finished():
            self.bouncing = False
            # after bounce, pick new short target so it doesn't re-freeze
            self.target_x = None
        anim.finished.connect(on_finished)
        anim.start(QtCore.QAbstractAnimation.DeleteWhenStopped)

def run():
    app = QtWidgets.QApplication(sys.argv)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)

    gif_path = find_first_gif()
    if not gif_path:
        QtWidgets.QMessageBox.critical(None, "GIF not found",
            "Put a GIF (dino.gif or any .gif) in the same folder and run again.")
        return

    try:
        right_bytes, left_bytes, size_tuple = build_right_left_gifs(gif_path, scale=SCALE)
    except Exception as e:
        traceback.print_exc()
        QtWidgets.QMessageBox.critical(None, "GIF processing failed", f"Failed to process GIF: {e}")
        return

    try:
        widget = SmoothGifPet(right_bytes, left_bytes, size_tuple, raise_pet=RAISE_PET)
    except Exception as e:
        traceback.print_exc()
        QtWidgets.QMessageBox.critical(None, "Playback failed", f"Failed to create pet widget: {e}")
        return

    # keep event loop alive; Ctrl+C works
    keep = QtCore.QTimer(); keep.start(1000); keep.timeout.connect(lambda: None)
    sys.exit(app.exec_())

if __name__ == "__main__":
    run()


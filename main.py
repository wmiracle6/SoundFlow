import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pygame
import sqlite3
import io
import os
import bcrypt
import time
import tempfile
from PIL import Image, ImageTk

# === Для обложек из MP3 (опционально) ===
try:
    from mutagen import File as MutagenFile
    from mutagen.id3 import APIC
    from mutagen.mp4 import MP4Cover
except ImportError:
    MutagenFile = None

# Цвета (холодная палитра)
BG_COLOR = "#006363"
ACCENT_COLOR = "#009999"
DARK_ACCENT = "#1D7373"
LIGHT_ACCENT = "#33CCCC"
EXTRA_LIGHT = "#5CCCCC"
TEXT_COLOR = "white"

DB_PATH = "music_app.db"
SESSION_FILE = "session.txt"

pygame.mixer.init()


# ----------------------------
# БАЗА ДАННЫХ
# ----------------------------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            avatar_path TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS track (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            audio_data BLOB NOT NULL,
            cover_data BLOB
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS playlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            track_id INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES user(id) ON DELETE CASCADE,
            FOREIGN KEY(track_id) REFERENCES track(id) ON DELETE CASCADE,
            UNIQUE(user_id, track_id)
        )
    """)
    conn.commit()
    conn.close()


def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())


def verify_password(password, hashed):
    return bcrypt.checkpw(password.encode('utf-8'), hashed)


def extract_album_art_from_mp3(mp3_path):
    if MutagenFile is None:
        return None
    try:
        audio = MutagenFile(mp3_path)
        if audio is None:
            return None
        # Для MP3 файлов (ID3 теги)
        if hasattr(audio, 'tags') and audio.tags:
            for tag_name, tag_value in audio.tags.items():
                if tag_name.startswith('APIC'):
                    if hasattr(tag_value, 'data'):
                        return tag_value.data
        # Для MP4 файлов (M4A и др.)
        elif hasattr(audio, 'tags') and audio.tags:
            covr_data = audio.get("covr", [])
            if covr_data:
                cover = covr_data[0]
                if isinstance(cover, MP4Cover):
                    return cover
                elif isinstance(cover, bytes):
                    return cover
    except Exception:
        pass
    return None


def format_time(seconds):
    if seconds < 0 or seconds == float('inf'):
        return "-:---"
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02}"


# ----------------------------
# КАСТОМНЫЙ ПОЛЗУНОК
# ----------------------------

class CustomSlider(tk.Canvas):
    def __init__(self, parent, from_=0, to=100, command=None, **kwargs):
        kwargs.setdefault("height", 16)
        kwargs.setdefault("bg", BG_COLOR)
        kwargs.setdefault("highlightthickness", 0)
        super().__init__(parent, **kwargs)

        self.from_ = from_
        self.to = to
        self.command = command
        self.value = from_
        self.dragging = False

        self.track_color = DARK_ACCENT
        self.fill_color = ACCENT_COLOR
        self.thumb_color = ACCENT_COLOR
        self.thumb_radius = 6
        self.padding = self.thumb_radius + 2

        self.bind("<Button-1>", self.on_click)
        self.bind("<B1-Motion>", self.on_drag)
        self.bind("<ButtonRelease-1>", self.on_release)
        self.bind("<Configure>", self.redraw)
        self.redraw()

    def set(self, value):
        old_val = self.value
        self.value = max(self.from_, min(self.to, value))
        if old_val != self.value:
            self.redraw()
            if self.command:
                self.command(self.value)

    def get(self):
        return self.value

    def on_click(self, event):
        self.dragging = True
        self.update_value(event.x)

    def on_drag(self, event):
        if self.dragging:
            self.update_value(event.x)

    def on_release(self, event):
        self.dragging = False

    def update_value(self, x):
        width = self.winfo_width()
        if width <= 2 * self.padding:
            return
        usable_width = width - 2 * self.padding
        ratio = max(0.0, min(1.0, (x - self.padding) / usable_width))
        new_value = self.from_ + ratio * (self.to - self.from_)
        self.set(new_value)

    def redraw(self, event=None):
        self.delete("all")
        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 0 or height <= 0:
            return

        mid_y = height // 2
        usable_width = width - 2 * self.padding

        # Track
        self.create_line(0, mid_y, width, mid_y, fill=self.track_color, width=4)

        # Fill
        fill_ratio = (self.value - self.from_) / (self.to - self.from_)
        fill_width = self.padding + fill_ratio * usable_width
        if fill_width > self.padding:
            self.create_line(self.padding, mid_y, fill_width, mid_y, fill=self.fill_color, width=4)

        # Thumb
        thumb_x = self.padding + fill_ratio * usable_width
        self.create_oval(
            thumb_x - self.thumb_radius,
            mid_y - self.thumb_radius,
            thumb_x + self.thumb_radius,
            mid_y + self.thumb_radius,
            fill=self.thumb_color,
            outline=""
        )


# ----------------------------
# ЭКРАН ВХОДА
# ----------------------------

class LoginWindow:
    def __init__(self, root):
        self.root = root
        self.root.title("SoundFlow — Вход")
        self.root.geometry("400x400")
        self.root.configure(bg=BG_COLOR)
        self.center_window()
        self.create_widgets()

    def center_window(self):
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() // 2) - (400 // 2)
        y = (self.root.winfo_screenheight() // 2) - (400 // 2)
        self.root.geometry(f"400x400+{x}+{y}")

    def create_widgets(self):
        title = ttk.Label(self.root, text="SoundFlow", font=("Arial", 20, "bold"), background=BG_COLOR,
                          foreground=TEXT_COLOR)
        title.pack(pady=20)

        ttk.Label(self.root, text="Логин или Email:", background=BG_COLOR, foreground=TEXT_COLOR).pack(pady=(20, 5))
        self.login_var = tk.StringVar()
        login_entry = ttk.Entry(self.root, textvariable=self.login_var, width=30)
        login_entry.pack()

        ttk.Label(self.root, text="Пароль:", background=BG_COLOR, foreground=TEXT_COLOR).pack(pady=(10, 5))
        self.password_var = tk.StringVar()
        pwd_entry = ttk.Entry(self.root, textvariable=self.password_var, show="*", width=30)
        pwd_entry.pack()

        btn_frame = ttk.Frame(self.root, style="Card.TFrame")
        btn_frame.pack(pady=20)

        ttk.Button(btn_frame, text="Войти", command=self.login, style="Accent.TButton").pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Регистрация", command=self.open_register, style="Accent.TButton").pack(side="left",
                                                                                                           padx=5)

        style = ttk.Style()
        style.configure("TFrame", background=BG_COLOR)
        style.configure("Card.TFrame", background=BG_COLOR)
        style.configure("Accent.TButton", background=ACCENT_COLOR, foreground="white", borderwidth=0, padding=6)

    def login(self):
        identifier = self.login_var.get().strip()
        password = self.password_var.get()

        if not identifier or not password:
            messagebox.showwarning("Ошибка", "Заполните все поля")
            return

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, password_hash FROM user WHERE username = ? OR email = ?",
                       (identifier, identifier))
        row = cursor.fetchone()
        conn.close()

        if row and verify_password(password, row[2]):
            with open(SESSION_FILE, "w") as f:
                f.write(str(row[0]))
            self.root.destroy()
            MusicApp(tk.Tk(), row[0], row[1])
        else:
            messagebox.showerror("Ошибка", "Неверный логин или пароль")

    def open_register(self):
        self.root.destroy()
        RegisterWindow(tk.Tk())


# ----------------------------
# ЭКРАН РЕГИСТРАЦИИ
# ----------------------------

class RegisterWindow:
    def __init__(self, root):
        self.root = root
        self.root.title("SoundFlow — Регистрация")
        self.root.geometry("400x450")
        self.root.configure(bg=BG_COLOR)
        self.center_window()
        self.create_widgets()

    def center_window(self):
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() // 2) - (400 // 2)
        y = (self.root.winfo_screenheight() // 2) - (450 // 2)
        self.root.geometry(f"400x450+{x}+{y}")

    def create_widgets(self):
        title = ttk.Label(self.root, text="Регистрация", font=("Arial", 20, "bold"), background=BG_COLOR,
                          foreground=TEXT_COLOR)
        title.pack(pady=20)

        ttk.Label(self.root, text="Логин:", background=BG_COLOR, foreground=TEXT_COLOR).pack(pady=(10, 5))
        self.username_var = tk.StringVar()
        ttk.Entry(self.root, textvariable=self.username_var, width=30).pack()

        ttk.Label(self.root, text="Email:", background=BG_COLOR, foreground=TEXT_COLOR).pack(pady=(10, 5))
        self.email_var = tk.StringVar()
        ttk.Entry(self.root, textvariable=self.email_var, width=30).pack()

        ttk.Label(self.root, text="Пароль:", background=BG_COLOR, foreground=TEXT_COLOR).pack(pady=(10, 5))
        self.password_var = tk.StringVar()
        ttk.Entry(self.root, textvariable=self.password_var, show="*", width=30).pack()

        ttk.Button(self.root, text="Зарегистрироваться", command=self.register, style="Accent.TButton").pack(pady=20)
        ttk.Button(self.root, text="← Назад", command=self.back_to_login, style="Accent.TButton").pack()

        style = ttk.Style()
        style.configure("Accent.TButton", background=ACCENT_COLOR, foreground="white", borderwidth=0, padding=6)

    def register(self):
        username = self.username_var.get().strip()
        email = self.email_var.get().strip()
        password = self.password_var.get()

        if not username or not email or not password:
            messagebox.showwarning("Ошибка", "Все поля обязательны")
            return
        if len(password) < 6:
            messagebox.showwarning("Ошибка", "Пароль должен быть не короче 6 символов")
            return

        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            hashed = hash_password(password)
            cursor.execute("INSERT INTO user (username, email, password_hash) VALUES (?, ?, ?)",
                           (username, email, hashed))
            conn.commit()
            conn.close()
            messagebox.showinfo("Успех", "Регистрация прошла успешно!")
            self.back_to_login()
        except sqlite3.IntegrityError:
            messagebox.showerror("Ошибка", "Логин или email уже заняты")

    def back_to_login(self):
        self.root.destroy()
        LoginWindow(tk.Tk())


# ----------------------------
# ОСНОВНОЕ ПРИЛОЖЕНИЕ
# ----------------------------

class MusicApp:
    def __init__(self, root, user_id, username):
        self.root = root
        self.user_id = user_id
        self.username = username
        self.root.title(f"SoundFlow — {username}")
        self.root.geometry("950x700")
        self.root.configure(bg=BG_COLOR)
        self.root.minsize(800, 600)

        self.tracks = self.load_tracks_from_db()
        self.current_track = None
        self.current_source = None
        self.is_playing = False
        self.playlist_tracks = []
        self.seeking = False
        self.track_length = 0
        # --- Новые переменные для синхронизации времени ---
        self.start_time_ticks = 0
        self.offset_ms = 0
        # --- Путь к временному файлу ---
        self._temp_file_path = None
        # -----------------------------------------------

        self.load_user_data()
        self.load_playlist_from_db()

        self.setup_styles()
        self.create_widgets()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Инициализация громкости и состояния отключения звука
        self.volume_slider.set(70)
        self.on_volume_change(70)
        self._is_muted = False
        self._last_volume = 70

        # Запуск обновления прогресса
        self.update_progress()

    def on_close(self):
        # Убедимся, что временный файл удален при закрытии
        self._cleanup_temp_file()
        if os.path.exists(SESSION_FILE):
            os.remove(SESSION_FILE)
        self.root.destroy()

    def _cleanup_temp_file(self):
        if self._temp_file_path and os.path.exists(self._temp_file_path):
            try:
                os.remove(self._temp_file_path)
                self._temp_file_path = None
            except OSError:
                pass  # Игнорируем ошибки при удалении

    def setup_styles(self):
        style = ttk.Style()
        style.theme_use("default")
        style.configure("TNotebook", background=BG_COLOR)
        style.configure("TNotebook.Tab", background=DARK_ACCENT, foreground=TEXT_COLOR, padding=[14, 8])
        style.map("TNotebook.Tab", background=[("selected", ACCENT_COLOR)], foreground=[("selected", "white")])
        style.configure("Card.TFrame", background=BG_COLOR)
        style.configure("Accent.TButton", background=ACCENT_COLOR, foreground="white", borderwidth=0, padding=6)
        style.configure("Header.TLabel", background=BG_COLOR, foreground=TEXT_COLOR, font=("Arial", 11, "bold"))

    def load_user_data(self):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT username, email, avatar_path FROM user WHERE id = ?", (self.user_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            self.username = row[0]
            self.email = row[1]
            self.avatar_path = row[2]

    def load_tracks_from_db(self):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, artist, cover_data FROM track")
        rows = cursor.fetchall()
        conn.close()
        return [{"id": r[0], "title": r[1], "artist": r[2], "art_data": r[3]} for r in rows]

    def load_playlist_from_db(self):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT t.id, t.title, t.artist, t.cover_data
            FROM playlist p
            JOIN track t ON p.track_id = t.id
            WHERE p.user_id = ?
        """, (self.user_id,))
        rows = cursor.fetchall()
        conn.close()
        self.playlist_tracks = [{"id": r[0], "title": r[1], "artist": r[2], "art_data": r[3]} for r in rows]

    def set_album_art_for_label(self, label, art_data, size=45):
        if art_data:
            try:
                img = Image.open(io.BytesIO(art_data)).convert("RGB")
                img.thumbnail((size, size))
                photo = ImageTk.PhotoImage(img)
                label.config(image=photo, text="", width=size, height=size)
                label.image = photo
            except Exception:
                label.config(image="", text="🎵", fg=EXTRA_LIGHT, font=("Arial", 12),
                             width=3, height=2, compound="center")
        else:
            label.config(image="", text="🎵", fg=EXTRA_LIGHT, font=("Arial", 12),
                         width=3, height=2, compound="center")

    def create_widgets(self):
        self.build_player_bar()

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(expand=True, fill="both", padx=10, pady=10)

        self.tab_home = ttk.Frame(self.notebook, style="Card.TFrame")
        self.tab_playlist = ttk.Frame(self.notebook, style="Card.TFrame")
        self.tab_profile = ttk.Frame(self.notebook, style="Card.TFrame")

        self.notebook.add(self.tab_home, text="Главная")
        self.notebook.add(self.tab_playlist, text="Мой плейлист")
        self.notebook.add(self.tab_profile, text="Профиль")

        self.build_home_tab()
        self.build_playlist_tab()
        self.build_profile_tab()

    def build_home_tab(self):
        top_frame = ttk.Frame(self.tab_home, style="Card.TFrame")
        top_frame.pack(pady=10, fill="x", padx=20)

        import_btn = ttk.Button(top_frame, text="➕ Импортировать трек", command=self.import_track,
                                style="Accent.TButton")
        import_btn.pack(side="left")

        search_frame = ttk.Frame(self.tab_home, style="Card.TFrame")
        search_frame.pack(pady=10, fill="x", padx=20)

        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=50, font=("Arial", 10))
        search_entry.pack(side="left", padx=(0, 10))
        search_btn = ttk.Button(search_frame, text="🔍 Поиск", command=self.search_music, style="Accent.TButton")
        search_btn.pack(side="left")

        canvas_frame = ttk.Frame(self.tab_home, style="Card.TFrame")
        canvas_frame.pack(fill="both", expand=True, padx=20, pady=10)

        self.home_canvas = tk.Canvas(canvas_frame, bg=BG_COLOR, highlightthickness=0)
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.home_canvas.yview)
        self.home_scrollable = ttk.Frame(self.home_canvas, style="Card.TFrame")

        self.home_scrollable.bind("<Configure>",
                                  lambda e: self.home_canvas.configure(scrollregion=self.home_canvas.bbox("all")))
        self.home_canvas.create_window((0, 0), window=self.home_scrollable, anchor="nw")
        self.home_canvas.configure(yscrollcommand=scrollbar.set)

        self.home_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.bind_mousewheel(self.home_canvas)
        self.display_home_tracks(self.tracks)

    def display_home_tracks(self, tracks):
        for widget in self.home_scrollable.winfo_children():
            widget.destroy()

        if not tracks:
            lbl = ttk.Label(self.home_scrollable, text="Нет треков", foreground=EXTRA_LIGHT, background=BG_COLOR)
            lbl.pack(pady=30)
            return

        for track in tracks:
            card = ttk.Frame(self.home_scrollable, style="Card.TFrame")
            card.pack(fill="x", padx=5, pady=7)

            art_label = tk.Label(card, bg=DARK_ACCENT, width=3, height=2, font=("Arial", 12))
            art_label.pack(side="left", padx=(0, 12), pady=5)
            self.set_album_art_for_label(art_label, track.get("art_data"), size=45)

            info_frame = ttk.Frame(card, style="Card.TFrame")
            info_frame.pack(side="left", fill="x", expand=True)

            title_lbl = ttk.Label(info_frame, text=track["title"], style="Header.TLabel")
            title_lbl.pack(anchor="w")
            artist_lbl = ttk.Label(info_frame, text=track["artist"], foreground=LIGHT_ACCENT, background=BG_COLOR)
            artist_lbl.pack(anchor="w")

            add_btn = ttk.Button(card, text="➕", command=lambda t=track: self.add_to_playlist(t),
                                 style="Accent.TButton", width=4)
            add_btn.pack(side="right", padx=(0, 10))

            for widget in [card, art_label, title_lbl, artist_lbl]:
                widget.bind("<Double-1>", lambda e, t=track: self.play_track(t, source="home"))

    def build_playlist_tab(self):
        canvas_frame = ttk.Frame(self.tab_playlist, style="Card.TFrame")
        canvas_frame.pack(fill="both", expand=True, padx=20, pady=10)

        self.playlist_canvas = tk.Canvas(canvas_frame, bg=BG_COLOR, highlightthickness=0)
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.playlist_canvas.yview)
        self.playlist_scrollable = ttk.Frame(self.playlist_canvas, style="Card.TFrame")

        self.playlist_scrollable.bind("<Configure>", lambda e: self.playlist_canvas.configure(
            scrollregion=self.playlist_canvas.bbox("all")))
        self.playlist_canvas.create_window((0, 0), window=self.playlist_scrollable, anchor="nw")
        self.playlist_canvas.configure(yscrollcommand=scrollbar.set)

        self.playlist_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.bind_mousewheel(self.playlist_canvas)
        self.refresh_playlist()

    def refresh_playlist(self):
        for widget in self.playlist_scrollable.winfo_children():
            widget.destroy()

        self.load_playlist_from_db()

        if not self.playlist_tracks:
            lbl = ttk.Label(self.playlist_scrollable, text="Плейлист пуст", foreground=EXTRA_LIGHT, background=BG_COLOR,
                            font=("Arial", 10, "italic"))
            lbl.pack(pady=40)
            return

        for track in self.playlist_tracks:
            card = ttk.Frame(self.playlist_scrollable, style="Card.TFrame")
            card.pack(fill="x", padx=5, pady=7)

            art_label = tk.Label(card, bg=DARK_ACCENT, width=3, height=2, font=("Arial", 12))
            art_label.pack(side="left", padx=(0, 12), pady=5)
            self.set_album_art_for_label(art_label, track.get("art_data"), size=45)

            info_frame = ttk.Frame(card, style="Card.TFrame")
            info_frame.pack(side="left", fill="x", expand=True)

            title_lbl = ttk.Label(info_frame, text=track["title"], style="Header.TLabel")
            title_lbl.pack(anchor="w")
            artist_lbl = ttk.Label(info_frame, text=track["artist"], foreground=LIGHT_ACCENT, background=BG_COLOR)
            artist_lbl.pack(anchor="w")

            del_btn = ttk.Button(card, text="❌", command=lambda t=track: self.remove_from_playlist(t),
                                 style="Accent.TButton", width=4)
            del_btn.pack(side="right", padx=(0, 10))

            for widget in [card, art_label, title_lbl, artist_lbl]:
                widget.bind("<Double-1>", lambda e, t=track: self.play_track(t, source="playlist"))

    def build_profile_tab(self):
        profile_frame = ttk.Frame(self.tab_profile, style="Card.TFrame")
        profile_frame.pack(pady=30)

        self.avatar_display = tk.Label(profile_frame, width=150, height=150, bg=DARK_ACCENT)
        self.avatar_display.grid(row=0, column=0, rowspan=6, padx=20, pady=10)
        self.load_avatar_image()

        avatar_btn = ttk.Button(profile_frame, text="Загрузить аватар", command=self.upload_avatar,
                                style="Accent.TButton")
        avatar_btn.grid(row=6, column=0, pady=10)

        self.username_var = tk.StringVar(value=self.username)
        self.email_var = tk.StringVar(value=self.email)
        self.old_password_var = tk.StringVar()
        self.new_password_var = tk.StringVar()

        ttk.Label(profile_frame, text="Логин:", style="Header.TLabel").grid(row=0, column=1, sticky="e", padx=10,
                                                                            pady=8)
        ttk.Entry(profile_frame, textvariable=self.username_var, width=30).grid(row=0, column=2, padx=10)

        ttk.Label(profile_frame, text="Email:", style="Header.TLabel").grid(row=1, column=1, sticky="e", padx=10,
                                                                            pady=8)
        ttk.Entry(profile_frame, textvariable=self.email_var, width=30).grid(row=1, column=2, padx=10)

        ttk.Label(profile_frame, text="Старый пароль:", style="Header.TLabel").grid(row=2, column=1, sticky="e",
                                                                                    padx=10, pady=8)
        ttk.Entry(profile_frame, textvariable=self.old_password_var, show="*", width=30).grid(row=2, column=2, padx=10)

        ttk.Label(profile_frame, text="Новый пароль:", style="Header.TLabel").grid(row=3, column=1, sticky="e", padx=10,
                                                                                   pady=8)
        ttk.Entry(profile_frame, textvariable=self.new_password_var, show="*", width=30).grid(row=3, column=2, padx=10)

        save_btn = ttk.Button(profile_frame, text="💾 Сохранить", command=self.save_profile, style="Accent.TButton")
        save_btn.grid(row=4, column=2, pady=15, sticky="e")

        logout_btn = ttk.Button(profile_frame, text="🚪 Выйти", command=self.logout, style="Accent.TButton")
        logout_btn.grid(row=5, column=2, pady=5, sticky="e")

    def load_avatar_image(self):
        if self.avatar_path and os.path.exists(self.avatar_path):
            try:
                img = Image.open(self.avatar_path).resize((140, 140))
                self.avatar_img = ImageTk.PhotoImage(img)
                self.avatar_display.config(image=self.avatar_img)
            except Exception:
                self.avatar_display.config(image="", text="📷\nАватар", fg=EXTRA_LIGHT, font=("Arial", 14))
        else:
            self.avatar_display.config(image="", text="📷\nАватар", fg=EXTRA_LIGHT, font=("Arial", 14))

    def build_player_bar(self):
        player_frame = ttk.Frame(self.root, style="Card.TFrame")
        player_frame.pack(side="bottom", fill="x", padx=20, pady=10)

        self.player_art_label = tk.Label(player_frame, bg=BG_COLOR, width=3, height=2, font=("Arial", 12))
        self.player_art_label.pack(side="left", padx=(0, 15), pady=5)

        right_frame = ttk.Frame(player_frame, style="Card.TFrame")
        right_frame.pack(side="left", fill="x", expand=True)

        self.player_title_label = ttk.Label(right_frame, text="Нет трека", style="Header.TLabel")
        self.player_title_label.pack(anchor="w", pady=(0, 2))
        self.player_artist_label = ttk.Label(right_frame, text="", foreground=LIGHT_ACCENT, background=BG_COLOR)
        self.player_artist_label.pack(anchor="w", pady=(0, 5))

        control_frame = ttk.Frame(right_frame, style="Card.TFrame")
        control_frame.pack(fill="x", pady=(0, 5))

        vol_frame = ttk.Frame(control_frame, style="Card.TFrame")
        vol_frame.pack(side="left", padx=(0, 10))

        # Иконка громкости — кликабельная
        self.vol_icon = tk.Label(vol_frame, text="🔊", fg=EXTRA_LIGHT, bg=BG_COLOR, font=("Arial", 12), cursor="hand2")
        self.vol_icon.pack(side="left", padx=(0, 5))
        self.vol_icon.bind("<Button-1>", self.toggle_mute)

        self.volume_slider = CustomSlider(
            vol_frame,
            from_=0,
            to=100,
            command=self.on_volume_change,
            width=80
        )
        self.volume_slider.set(70)
        self.volume_slider.pack(side="left")

        self.progress = CustomSlider(
            control_frame,
            from_=0,
            to=100,
            command=self.on_progress_drag
        )
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.progress.bind("<ButtonPress-1>", self.on_progress_press)
        self.progress.bind("<ButtonRelease-1>", self.on_progress_release)

        self.time_label = ttk.Label(control_frame, text="0:00 / -:---", background=BG_COLOR, foreground=EXTRA_LIGHT,
                                    font=("Arial", 9))
        self.time_label.pack(side="left", padx=(10, 0))

        button_frame = ttk.Frame(control_frame, style="Card.TFrame")
        button_frame.pack(side="right")

        self.prev_btn = ttk.Button(button_frame, text="⏮", command=self.prev_track, style="Accent.TButton", width=4)
        self.prev_btn.pack(side="left", padx=2)

        self.play_btn = ttk.Button(button_frame, text="▶", command=self.toggle_play, style="Accent.TButton", width=4)
        self.play_btn.pack(side="left", padx=2)

        self.next_btn = ttk.Button(button_frame, text="⏭", command=self.next_track, style="Accent.TButton", width=4)
        self.next_btn.pack(side="left", padx=2)

        self.add_to_playlist_btn = ttk.Button(button_frame, text="➕", command=self.toggle_playlist_add,
                                              style="Accent.TButton", width=4)
        self.add_to_playlist_btn.pack(side="left", padx=(8, 0))

        self.update_player_display()

    def on_progress_drag(self, value):
        self.seeking = True
        # Обновляем время при перетаскивании
        if self.track_length > 0:
            target_time = (float(value) / 100.0) * self.track_length
            self.time_label.config(text=f"{format_time(target_time)} / {format_time(self.track_length)}")

    def toggle_mute(self, event=None):
        if self._is_muted:
            self.volume_slider.set(self._last_volume)
            self.vol_icon.config(text="🔊")
            self._is_muted = False
        else:
            self._last_volume = self.volume_slider.get()
            self.volume_slider.set(0)
            self.vol_icon.config(text="🔇")
            self._is_muted = True
        self.on_volume_change(self.volume_slider.get())

    def on_volume_change(self, value):
        volume = float(value) / 100.0
        pygame.mixer.music.set_volume(volume)
        if value == 0:
            self.vol_icon.config(text="🔇")
        else:
            self.vol_icon.config(text="🔊")

    def update_player_display(self):
        if self.current_track:
            self.set_album_art_for_label(self.player_art_label, self.current_track.get("art_data"), size=45)
            self.player_title_label.config(text=self.current_track["title"])
            self.player_artist_label.config(text=self.current_track["artist"])
            in_playlist = any(t["id"] == self.current_track["id"] for t in self.playlist_tracks)
            self.add_to_playlist_btn.config(text="✅" if in_playlist else "➕")
        else:
            self.player_art_label.config(image="", text="🎵", fg=EXTRA_LIGHT, font=("Arial", 12),
                                         width=3, height=2, compound="center")
            self.player_title_label.config(text="Нет трека")
            self.player_artist_label.config(text="")
            self.add_to_playlist_btn.config(text="➕")
            self.time_label.config(text="0:00 / -:---")

    def toggle_playlist_add(self):
        if self.current_track is None:
            return
        in_playlist = any(t["id"] == self.current_track["id"] for t in self.playlist_tracks)
        if not in_playlist:
            self.add_to_playlist(self.current_track)
        else:
            self.remove_from_playlist(self.current_track)

    def on_progress_press(self, event=None):
        self.seeking = True

    def on_progress_release(self, event=None):
        if self.current_track and self.track_length > 0 and self.seeking:
            percent = float(self.progress.get())
            target_time = (percent / 100.0) * self.track_length
            # --- Новая логика перемотки через временный файл ---
            # Останавливаем музыку
            pygame.mixer.music.stop()

            # Загружаем аудио-данные из БД
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT audio_data FROM track WHERE id = ?", (self.current_track["id"],))
            row = cursor.fetchone()
            conn.close()

            if row:
                audio_data = row[0]
                # Создаем новый временный файл
                self._cleanup_temp_file()  # Удаляем старый, если он был
                try:
                    # Создаем временный файл с расширением .mp3
                    temp_file_handle, temp_path = tempfile.mkstemp(suffix='.mp3')
                    with os.fdopen(temp_file_handle, 'wb') as temp_file:
                        temp_file.write(audio_data)
                    self._temp_file_path = temp_path
                except OSError:
                    print("Не удалось создать временный файл для воспроизведения.")
                    return

                try:
                    pygame.mixer.music.load(self._temp_file_path)
                    # Воспроизводим с новой позиции
                    # Используем startpos, если он поддерживается. В pygame 2.6.1 он должен быть.
                    # Если все еще будет ошибка, можно попробовать сначала play(), затем set_pos().
                    pygame.mixer.music.play(startpos=target_time)
                    # Обновляем внутренние таймеры
                    self.start_time_ticks = pygame.time.get_ticks()
                    self.offset_ms = target_time * 1000
                    # Обновляем UI
                    self.time_label.config(text=f"{format_time(target_time)} / {format_time(self.track_length)}")
                    self.progress.set(percent)
                    # Убедимся, что is_playing = True
                    if not self.is_playing:
                        self.is_playing = True
                        self.play_btn.config(text="⏸")
                except pygame.error as e:
                    print(f"Ошибка загрузки/воспроизведения при перемотке: {e}")
                    # Восстанавливаем предыдущее состояние, если перемотка не удалась
                    # Загружаем снова, но с начала
                    pygame.mixer.music.load(self._temp_file_path)
                    pygame.mixer.music.play()
                    self.start_time_ticks = pygame.time.get_ticks()
                    self.offset_ms = 0
                    current_pos_s = 0
                    self.time_label.config(text=f"{format_time(current_pos_s)} / {format_time(self.track_length)}")
                    self.progress.set(0)
                except TypeError:
                    # Если startpos не поддерживается, обрабатываем как ошибку
                    print(f"startpos не поддерживается. Попробуем play + set_pos.")
                    # Попробуем альтернативный способ
                    pygame.mixer.music.load(self._temp_file_path)
                    pygame.mixer.music.play()
                    try:
                        pygame.mixer.music.set_pos(target_time)
                        self.start_time_ticks = pygame.time.get_ticks()
                        self.offset_ms = target_time * 1000
                        self.time_label.config(text=f"{format_time(target_time)} / {format_time(self.track_length)}")
                        self.progress.set(percent)
                        if not self.is_playing:
                            self.is_playing = True
                            self.play_btn.config(text="⏸")
                    except pygame.error:
                        print("set_pos также не сработал.")
                        # Возвращаемся к началу
                        self.start_time_ticks = pygame.time.get_ticks()
                        self.offset_ms = 0
                        current_pos_s = 0
                        self.time_label.config(text=f"{format_time(current_pos_s)} / {format_time(self.track_length)}")
                        self.progress.set(0)
            # --------------------------------
        self.seeking = False

    def get_track_length_from_blob(self, audio_data):
        try:
            if MutagenFile:
                import tempfile
                with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as f:
                    f.write(audio_data)
                    temp_path = f.name
                try:
                    audio_file = MutagenFile(temp_path)
                    length = audio_file.info.length if audio_file and hasattr(audio_file.info, 'length') else -1
                except:
                    length = -1
                os.unlink(temp_path)
                return length
            else:
                # Альтернативный метод через pygame.mixer.Sound - может быть неточным
                temp_file_handle, temp_path = tempfile.mkstemp(suffix='.mp3')
                with os.fdopen(temp_file_handle, 'wb') as temp_file:
                    temp_file.write(audio_data)
                try:
                    sound = pygame.mixer.Sound(temp_path)
                    length = sound.get_length()
                except:
                    length = -1
                os.unlink(temp_path)
                return length
        except Exception:
            return -1

    def update_progress(self):
        if self.current_track and self.is_playing and not self.seeking:
            current_time_ticks = pygame.time.get_ticks()
            elapsed_time_ms = current_time_ticks - self.start_time_ticks
            current_pos_ms = self.offset_ms + elapsed_time_ms
            current_pos_s = current_pos_ms / 1000.0

            if self.track_length > 0:
                if current_pos_s >= self.track_length:
                    self.next_track()
                    return
                percent = (current_pos_s / self.track_length) * 100
                self.progress.set(percent)
                self.time_label.config(text=f"{format_time(current_pos_s)} / {format_time(self.track_length)}")
        elif self.current_track and not self.is_playing and not self.seeking:
            if self.track_length > 0:
                current_pos_s = self.offset_ms / 1000.0
                percent = (current_pos_s / self.track_length) * 100 if self.track_length > 0 else 0
                self.progress.set(percent)
                self.time_label.config(text=f"{format_time(current_pos_s)} / {format_time(self.track_length)}")

        self.root.after(500, self.update_progress)

    def search_music(self):
        query = self.search_var.get().lower()
        if not query:
            self.display_home_tracks(self.tracks)
        else:
            filtered = [t for t in self.tracks if query in t["title"].lower() or query in t["artist"].lower()]
            self.display_home_tracks(filtered)

    def add_to_playlist(self, track):
        if not any(t["id"] == track["id"] for t in self.playlist_tracks):
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("INSERT INTO playlist (user_id, track_id) VALUES (?, ?)", (self.user_id, track["id"]))
            conn.commit()
            conn.close()
            self.refresh_playlist()
            if self.current_track and self.current_track["id"] == track["id"]:
                self.update_player_display()

    def remove_from_playlist(self, track):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM playlist WHERE user_id = ? AND track_id = ?", (self.user_id, track["id"]))
        conn.commit()
        conn.close()
        self.refresh_playlist()
        if self.current_track and self.current_track["id"] == track["id"]:
            self.update_player_display()

    def upload_avatar(self):
        path = filedialog.askopenfilename(filetypes=[("Изображения", "*.png *.jpg *.jpeg *.gif")])
        if path:
            self.avatar_path = path
            self.save_user_data()

    def save_profile(self):
        username = self.username_var.get().strip()
        email = self.email_var.get().strip()
        old_pwd = self.old_password_var.get()
        new_pwd = self.new_password_var.get()

        if not username or not email:
            messagebox.showwarning("Ошибка", "Логин и email обязательны")
            return

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Проверка пароля при смене
        if new_pwd:
            cursor.execute("SELECT password_hash FROM user WHERE id = ?", (self.user_id,))
            row = cursor.fetchone()
            if not row or not verify_password(old_pwd, row[0]):
                messagebox.showerror("Ошибка", "Неверный старый пароль")
                conn.close()
                return
            if len(new_pwd) < 6:
                messagebox.showwarning("Ошибка", "Новый пароль должен быть не короче 6 символов")
                conn.close()
                return
            pwd_hash = hash_password(new_pwd)
            cursor.execute("UPDATE user SET password_hash = ? WHERE id = ?", (pwd_hash, self.user_id))

        # Обновление остального
        try:
            cursor.execute("UPDATE user SET username = ?, email = ?, avatar_path = ? WHERE id = ?",
                           (username, email, self.avatar_path, self.user_id))
            conn.commit()
            self.username = username
            self.email = email
            self.root.title(f"SoundFlow — {username}")
            self.load_avatar_image()
            messagebox.showinfo("Успех", "Данные сохранены")
        except sqlite3.IntegrityError:
            messagebox.showerror("Ошибка", "Логин или email уже заняты")
        finally:
            conn.close()

    def save_user_data(self):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("UPDATE user SET avatar_path = ? WHERE id = ?", (self.avatar_path, self.user_id))
        conn.commit()
        conn.close()

    def logout(self):
        self._cleanup_temp_file()
        if os.path.exists(SESSION_FILE):
            os.remove(SESSION_FILE)
        self.root.destroy()
        LoginWindow(tk.Tk())

    def import_track(self):
        path = filedialog.askopenfilename(filetypes=[("MP3 files", "*.mp3")])
        if not path:
            return

        filename = os.path.basename(path)
        if " - " in filename[:-4]:
            title, artist = filename[:-4].split(" - ", 1)
        else:
            title = filename[:-4]
            artist = "Неизвестен"

        with open(path, "rb") as f:
            audio_data = f.read()

        cover_data = extract_album_art_from_mp3(path)

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO track (title, artist, audio_data, cover_data)
            VALUES (?, ?, ?, ?)
        """, (title, artist, audio_data, cover_data))
        conn.commit()
        conn.close()

        self.tracks = self.load_tracks_from_db()
        self.display_home_tracks(self.tracks)

    def play_track(self, track, source="home"):
        # Очищаем старый временный файл перед загрузкой нового
        self._cleanup_temp_file()

        self.current_track = track
        self.current_source = source

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT audio_data FROM track WHERE id = ?", (track["id"],))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return

        audio_data = row[0]
        # Создаем временный файл
        try:
            temp_file_handle, temp_path = tempfile.mkstemp(suffix='.mp3')
            with os.fdopen(temp_file_handle, 'wb') as temp_file:
                temp_file.write(audio_data)
            self._temp_file_path = temp_path
        except OSError:
            print("Не удалось создать временный файл для воспроизведения.")
            return

        try:
            pygame.mixer.music.load(self._temp_file_path)
            pygame.mixer.music.play()
            self.is_playing = True
            self.play_btn.config(text="⏸")
            self.track_length = self.get_track_length_from_blob(audio_data)
            self.seeking = False

            self.start_time_ticks = pygame.time.get_ticks()
            self.offset_ms = 0
            self.progress.set(0)
            self.time_label.config(text=f"0:00 / {format_time(self.track_length)}")
        except Exception as e:
            print(f"Ошибка воспроизведения: {e}")
            self._cleanup_temp_file()  # Удаляем файл, если воспроизвести не удалось
            self.current_track = None
            self.update_player_display()
            return

        self.update_player_display()

    def toggle_play(self):
        if self.current_track is None:
            return
        if self.is_playing:
            pygame.mixer.music.pause()
            self.play_btn.config(text="▶")
            self.is_playing = False
            if self.track_length > 0:
                current_time_ticks = pygame.time.get_ticks()
                elapsed_time_ms = current_time_ticks - self.start_time_ticks
                self.offset_ms = self.offset_ms + elapsed_time_ms
        else:
            pygame.mixer.music.unpause()
            self.play_btn.config(text="⏸")
            self.is_playing = True
            self.start_time_ticks = pygame.time.get_ticks()

    def get_current_list(self):
        if self.current_source == "playlist":
            return self.playlist_tracks
        else:
            return self.tracks

    def prev_track(self):
        if not self.current_track:
            return
        current_list = self.get_current_list()
        if not current_list:
            return
        try:
            idx = next(i for i, t in enumerate(current_list) if t["id"] == self.current_track["id"])
            prev_idx = (idx - 1) % len(current_list)
            self.play_track(current_list[prev_idx], source=self.current_source)
        except StopIteration:
            pass

    def next_track(self):
        if not self.current_track:
            if self.playlist_tracks:
                self.play_track(self.playlist_tracks[0], source="playlist")
            elif self.tracks:
                self.play_track(self.tracks[0], source="home")
            return
        current_list = self.get_current_list()
        if not current_list:
            return
        try:
            idx = next(i for i, t in enumerate(current_list) if t["id"] == self.current_track["id"])
            next_idx = (idx + 1) % len(current_list)
            self.play_track(current_list[next_idx], source=self.current_source)
        except StopIteration:
            pass

    def _on_mousewheel(self, event, canvas):
        if event.num == 4 or event.delta > 0:
            canvas.yview_scroll(-1, "units")
        elif event.num == 5 or event.delta < 0:
            canvas.yview_scroll(1, "units")

    def bind_mousewheel(self, canvas):
        canvas.bind("<MouseWheel>", lambda e: self._on_mousewheel(e, canvas))
        canvas.bind("<Button-4>", lambda e: self._on_mousewheel(e, canvas))
        canvas.bind("<Button-5>", lambda e: self._on_mousewheel(e, canvas))


# ----------------------------
# ЗАПУСК ПРИЛОЖЕНИЯ
# ----------------------------

def main():
    init_db()

    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r") as f:
                user_id = int(f.read().strip())
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT username FROM user WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            conn.close()
            if row:
                root = tk.Tk()
                MusicApp(root, user_id, row[0])
                root.mainloop()
                return
        except Exception:
            if os.path.exists(SESSION_FILE):
                os.remove(SESSION_FILE)

    root = tk.Tk()
    LoginWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
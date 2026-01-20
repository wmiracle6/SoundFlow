import flet as ft
import flet_audio as ft_audio
import sqlite3
import sys
import os
import tempfile
import base64
import hashlib
import threading
import time
from mutagen import File as MutagenFile

# --- Константы стиля ---
BG_COLOR = "#121212"
SIDEBAR_COLOR = "#000000"
CARD_COLOR = "#181818"
MY_ACCENT = "#8bb7f0"
TEXT_SUB = "#B3B3B3"
DB_PATH = "music_app.db"


def get_db_path():
    # Если запущено как EXE
    if getattr(sys, 'frozen', False):
        # Папка, где лежит .exe
        base_dir = os.path.dirname(sys.executable)
    else:
        # Папка, где лежит .py (при разработке)
        base_dir = os.path.dirname(os.path.abspath(__file__))

    return os.path.join(base_dir, "music_app.db")

DB_PATH = get_db_path()

def resource_path(relative_path):
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

ICON_PATH = resource_path("icon2.ico")


def image_to_base64(data: bytes):
    try:
        if not data:
            return None
        return base64.b64encode(data).decode()
    except Exception as e:
        print(f"[image_to_base64 error] {e}")
        return None


def upload_system_gif(file_path):
    """Загрузка анимации (APNG или GIF) в БД"""
    try:
        if not os.path.exists(file_path):
            print(f"Файл {file_path} не найден, пропускаю загрузку.")
            return

        with open(file_path, "rb") as f:
            image_bytes = f.read()

        conn = sqlite3.connect(DB_PATH)
        # Мы оставляем ID 'equalizer', чтобы не менять логику в SoundFlowApp
        conn.execute("INSERT OR REPLACE INTO resources (id, data) VALUES (?, ?)", ("equalizer", image_bytes))
        conn.commit()
        conn.close()
        print(f"Анимация {file_path} успешно сохранена в базу!")
    except Exception as e:
        print(f"Ошибка при загрузке анимации: {e}")

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    # Таблица пользователей
    conn.execute("""CREATE TABLE IF NOT EXISTS user
                    (
                        id
                        INTEGER
                        PRIMARY
                        KEY
                        AUTOINCREMENT,
                        username
                        TEXT
                        UNIQUE,
                        email
                        TEXT,
                        password
                        TEXT,
                        avatar_path
                        TEXT
                    )""")

    # Таблица треков
    conn.execute("""CREATE TABLE IF NOT EXISTS track
                    (
                        id
                        INTEGER
                        PRIMARY
                        KEY
                        AUTOINCREMENT,
                        title
                        TEXT,
                        artist
                        TEXT,
                        audio_data
                        BLOB,
                        cover_data
                        BLOB,
                        duration
                        TEXT
                    )""")

    # --- НОВЫЕ ТАБЛИЦЫ ДЛЯ ПЛЕЙЛИСТОВ ---

    # 1. Таблица самих плейлистов
    conn.execute("""CREATE TABLE IF NOT EXISTS playlist
    (
        id
        INTEGER
        PRIMARY
        KEY
        AUTOINCREMENT,
        user_id
        INTEGER,
        name
        TEXT,
        cover_data
        BLOB,
        FOREIGN
        KEY
                    (
        user_id
                    ) REFERENCES user
                    (
                        id
                    )
        )""")

    # 2. Таблица связей треков и плейлистов (многие-ко-многим)
    conn.execute("""CREATE TABLE IF NOT EXISTS playlist_track
    (
        playlist_id
        INTEGER,
        track_id
        INTEGER,
        PRIMARY
        KEY
                    (
        playlist_id,
        track_id
                    ),
        FOREIGN KEY
                    (
                        playlist_id
                    ) REFERENCES playlist
                    (
                        id
                    ) ON DELETE CASCADE,
        FOREIGN KEY
                    (
                        track_id
                    ) REFERENCES track
                    (
                        id
                    )
                      ON DELETE CASCADE
        )""")

    # ------------------------------------

    # Таблица для системных файлов (твоя гифка)
    conn.execute("CREATE TABLE IF NOT EXISTS resources (id TEXT PRIMARY KEY, data BLOB)")

    cursor = conn.execute("PRAGMA table_info(track)")
    columns = [column[1] for column in cursor.fetchall()]
    if "album" not in columns:
        conn.execute("ALTER TABLE track ADD COLUMN album TEXT")

    if "equalizer_gif" not in columns:
        conn.execute("ALTER TABLE track ADD COLUMN equalizer_gif BLOB")

    conn.commit()
    conn.close()


class SoundFlowApp:
    def __init__(self, page: ft.Page):
        self.page = page

        # 1. Базовые переменные поиска и плейлистов
        self.search_query = ""
        self.search_timer = None
        self.current_temp_path = None
        self.temp_playlist_cover = None
        self.temp_avatar_bytes = None  # Для хранения выбранного, но не сохраненного фото

        # 2. Инициализация поиска (важно до загрузки интерфейса)
        self.search_field = ft.TextField(
            hint_text="Поиск треков или артистов...",
            prefix_icon=ft.Icons.SEARCH,
            width=400,
            height=45,
            border_color=MY_ACCENT,
            on_change=self.on_search_change,
            suffix=ft.IconButton(
                icon=ft.Icons.CLEAR,
                visible=False,
                on_click=lambda _: self.clear_search(self.search_field)
            )
        )
        self.playlist_search_field = ft.TextField(
            hint_text="Поиск в плейлисте...",
            prefix_icon=ft.Icons.SEARCH,
            width=400,
            height=45,
            border_color=MY_ACCENT,
            on_change=self.on_playlist_search_change,
            suffix=ft.IconButton(
                icon=ft.Icons.CLEAR,
                visible=False,
                on_click=lambda _: self.clear_search(self.playlist_search_field)
            )
        )

        # 3. Пикеры и overlay
        self.playlist_picker = ft.FilePicker(on_result=self.on_playlist_cover_picked)
        self.track_picker = ft.FilePicker(on_result=self.on_import_result)
        self.avatar_picker = ft.FilePicker(on_result=self.on_avatar_selected)
        self.reg_avatar_picker = ft.FilePicker(on_result=self.on_reg_avatar_selected)
        self.page.overlay.extend([
            self.playlist_picker, self.track_picker,
            self.avatar_picker, self.reg_avatar_picker
        ])

        # 4. Создаем аватар пользователя (тот самый, которого не хватало)
        self.user_avatar = ft.Container(
            width=40, height=40, border_radius=20,
            bgcolor="#222", clip_behavior=ft.ClipBehavior.HARD_EDGE,
            content=ft.Icon(ft.Icons.PERSON, size=20, color=TEXT_SUB)
        )

        # 5. Инициализируем колонку плейлистов
        self.playlists_column = ft.Column(scroll=ft.ScrollMode.AUTO, spacing=5)

        # 6. Системные переменные плеера
        self.user_id = None
        self.username = ""
        self.audio = None
        self.track_controls = {}
        self.all_tracks = []
        self.track_rows = {}
        self.is_shuffle = False
        self.shuffled_list = []
        self.current_track_id = None
        self.equalizer_b64 = None
        self.delete_mode = False
        self.selected_tracks = set()
        self.last_volume = 25
        self.is_muted = False
        self.temp_reg_avatar = None
        self.track_duration = 0
        self.is_seeking = False

        # 7. Настройка страницы
        self.page.bgcolor = BG_COLOR
        self.page.theme_mode = ft.ThemeMode.DARK
        self.page.padding = 0

        # 8. ЗАПУСКАЕМ создание кнопок плеера и ассетов
        self.load_assets()
        self.init_ui()  # Это создаст self.player_controls и self.main_view_container

        # 9. Показываем вход
        self.show_auth_screen()

    # --- АВТОРИЗАЦИЯ ---
    def show_auth_screen(self):
        self.page.clean()
        self.user_id = None
        login_in = ft.TextField(label="Логин", width=300, border_color=MY_ACCENT)
        pass_in = ft.TextField(label="Пароль", width=300, password=True, can_reveal_password=True,
                               border_color=MY_ACCENT)

        def login_click(e):
            h_pass = hash_password(pass_in.value)  # Хешируем ввод
            conn = sqlite3.connect(DB_PATH)
            user = conn.execute("SELECT id, username FROM user WHERE username=? AND password=?",
                                (login_in.value, h_pass)).fetchone()
            conn.close()
            if user:
                self.user_id, self.username = user[0], user[1]
                self.load_user_data()  # Загружаем email и avatar_path
                if self.avatar_path:
                    self.user_avatar.content = ft.Image(src_base64=self.avatar_path, fit=ft.ImageFit.COVER)
                self.show_main_app()
            else:
                self.page.snack_bar = ft.SnackBar(ft.Text("Неверный логин или пароль"), bgcolor="red")
                self.page.snack_bar.open = True
                self.page.update()

        # Центрирование через Column + Container
        self.page.add(
            ft.Container(
                content=ft.Column([
                    ft.Text("SoundFlow", size=45, weight="bold", color=MY_ACCENT),
                    ft.Container(height=10), # Отступ
                    login_in,
                    pass_in,
                    ft.Container(height=10),
                    ft.ElevatedButton("Войти", on_click=login_click, bgcolor=MY_ACCENT, color=SIDEBAR_COLOR, width=300, height=45),
                    ft.TextButton("Зарегистрироваться", on_click=lambda _: self.show_registration_screen())
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER, # Центрирует элементы по горизонтали
                alignment=ft.MainAxisAlignment.CENTER,            # Центрирует группу элементов по вертикали
                spacing=10),
                expand=True,
                alignment=ft.alignment.center # Центрирует всё содержимое контейнера на странице
            )
        )

    def clear_search(self, field):
        field.value = ""
        field.suffix.visible = False
        # Проверяем наличие на странице перед обновлением
        if field.page:
            field.update()

        # Вызываем логику поиска
        if field == self.search_field:
            self.on_search_change(
                ft.ControlEvent(target=field.uid, name="change", data="", control=field, page=self.page))
        else:
            self.on_playlist_search_change(
                ft.ControlEvent(target=field.uid, name="change", data="", control=field, page=self.page))

    def on_search_change(self, e):
        if e.control.page:
            e.control.suffix.visible = True if e.control.value else False
            e.control.update()

        # Получаем текст из поиска
        search_text = e.control.value.lower().strip()

        # Очищаем визуальный список и словарь ссылок
        self.tracks_list_view.controls = []
        self.track_rows.clear()

        # Фильтруем треки прямо в памяти (из self.all_tracks)
        for index, track in enumerate(self.all_tracks, start=1):
            # track[1] - название, track[2] - артист
            title = (track[1] or "").lower()
            artist = (track[2] or "").lower()

            if search_text in title or search_text in artist:
                # Создаем строку трека
                track_row = self.create_track_row(
                    index, track[0], track[1], track[2], track[3], track[4], track[5]
                )
                # Сохраняем ссылку для работы плеера
                self.track_rows[track[0]] = track_row
                self.tracks_list_view.controls.append(track_row)

        self.page.update()

    def on_playlist_cover_picked(self, e: ft.FilePickerResultEvent):
        if e.files:
            with open(e.files[0].path, "rb") as f:
                self.temp_playlist_cover = f.read()
            self.page.snack_bar = ft.SnackBar(ft.Text("Обложка для плейлиста выбрана!"))
            self.page.snack_bar.open = True
            self.page.update()

    def build_sidebar(self):
        # Инициализируем колонку, если она еще не создана
        if not hasattr(self, 'playlists_column'):
            self.playlists_column = ft.Column(scroll=ft.ScrollMode.AUTO, spacing=5)

        sidebar = ft.Container(
            width=250,
            bgcolor=SIDEBAR_COLOR,
            padding=ft.padding.only(top=20, left=10, right=10),
            content=ft.Column([
                ft.Container(
                    content=ft.Row([
                        self.user_avatar,
                        ft.Column([
                            ft.Text(self.username, weight="bold", size=16),
                            ft.Text("Аккаунт", color=MY_ACCENT, size=12),
                        ], spacing=0)
                    ], spacing=10),
                    on_click=lambda _: self.show_profile_view(),  # Теперь on_click у Container
                    ink=True,  # Добавляет эффект всплеска при нажатии
                    border_radius=10,
                    padding=5,
                ),

                ft.Divider(color="#282828", height=20),

                # Кнопка ПЛЮСИК
                ft.ListTile(
                    leading=ft.Icon(ft.Icons.ADD_BOX_OUTLINED, color=TEXT_SUB),
                    title=ft.Text("Создать плейлист", color=TEXT_SUB),
                    on_click=self.show_create_playlist_dialog
                ),

                ft.ListTile(
                    leading=ft.Icon(ft.Icons.HOME, color=MY_ACCENT),
                    title=ft.Text("Главная", color="white"),
                    on_click=lambda _: self.safe_navigate(self.load_main_view)
                ),

                ft.Container(height=10),
                ft.Text("   ВАШИ ПЛЕЙЛИСТЫ", size=11, color=TEXT_SUB, weight="bold"),

                # Сюда будут добавляться плейлисты из базы
                ft.Container(content=self.playlists_column, expand=True),

                ft.Divider(color="#282828"),
                ft.ListTile(
                    leading=ft.Icon(ft.Icons.LOGOUT, color=TEXT_SUB),
                    title=ft.Text("Выход", color=TEXT_SUB),
                    on_click=lambda _: self.logout()
                ),
            ], expand=True)
        )
        self.update_sidebar_playlists()  # Сразу наполняем список
        return sidebar

    def show_create_playlist_dialog(self, e):
        self.temp_playlist_cover = None
        name_input = ft.TextField(
            label="Название плейлиста",
            border_color=MY_ACCENT,
            autofocus=True
        )

        def save_playlist(e):
            if not name_input.value:
                name_input.error_text = "Введите название"
                name_input.update()
                return

            try:
                conn = sqlite3.connect(DB_PATH)
                conn.execute(
                    "INSERT INTO playlist (user_id, name, cover_data) VALUES (?, ?, ?)",
                    (self.user_id, name_input.value, self.temp_playlist_cover)
                )
                conn.commit()
                conn.close()

                # Закрываем диалог правильно
                dialog.open = False
                self.page.update()

                # Обновляем список в боковой панели
                self.update_sidebar_playlists()

                self.page.snack_bar = ft.SnackBar(ft.Text(f"Плейлист '{name_input.value}' создан!"), bgcolor="green")
                self.page.snack_bar.open = True
                self.page.update()
            except Exception as ex:
                print(f"Ошибка сохранения плейлиста: {ex}")

        # Создаем объект диалога
        dialog = ft.AlertDialog(
            title=ft.Text("Новый плейлист"),
            content=ft.Column([
                name_input,
                ft.ElevatedButton(
                    "Выбрать обложку",
                    icon=ft.Icons.IMAGE,
                    on_click=lambda _: self.playlist_picker.pick_files()
                ),
            ], tight=True, spacing=20),
            actions=[
                ft.TextButton("Отмена", on_click=lambda _: self.close_dialog()),
                ft.ElevatedButton("Создать", on_click=save_playlist, bgcolor=MY_ACCENT, color="black"),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.overlay.append(dialog)
        dialog.open = True
        self.page.update()

    def close_dialog(self):
        # Закрываем активный диалог
        if hasattr(self.page, "dialog") and self.page.dialog:
            self.page.dialog.open = False

        # Если диалог в overlay, ищем последний открытый и закрываем
        for control in self.page.overlay:
            if isinstance(control, ft.AlertDialog):
                control.open = False

        self.page.update()

    def show_registration_screen(self):
        self.page.clean()
        reg_login = ft.TextField(label="Логин", width=300, border_color=MY_ACCENT)
        reg_email = ft.TextField(label="Email", width=300, border_color=MY_ACCENT)
        reg_pass = ft.TextField(label="Пароль", width=300, password=True, border_color=MY_ACCENT)

        avatar_container = ft.Container(
            width=100, height=100, border_radius=50, bgcolor="#222",
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            content=ft.Image(src_base64=self.temp_reg_avatar, fit=ft.ImageFit.COVER) if self.temp_reg_avatar
            else ft.Icon(ft.Icons.PERSON, size=40, color=TEXT_SUB)
        )

        def register_click(e):
            # ВАЛИДАЦИЯ: Проверка на пустые поля
            if not reg_login.value or not reg_pass.value:
                self.page.snack_bar = ft.SnackBar(ft.Text("Логин и пароль не могут быть пустыми!"), bgcolor="orange")
                self.page.snack_bar.open = True
                self.page.update()
                return

            hashed_p = hash_password(reg_pass.value)

            try:
                conn = sqlite3.connect(DB_PATH)
                conn.execute("INSERT INTO user (username, password, email, avatar_path) VALUES (?, ?, ?, ?)",
                             (reg_login.value, hashed_p, reg_email.value, self.temp_reg_avatar))
                conn.commit()
                conn.close()
                self.page.snack_bar = ft.SnackBar(ft.Text("Регистрация успешна!"), bgcolor="green")
                self.page.snack_bar.open = True
                self.show_auth_screen()
            except sqlite3.IntegrityError:
                # ВАЛИДАЦИЯ: Обработка дубликата логина
                self.page.snack_bar = ft.SnackBar(ft.Text("Этот логин уже занят!"), bgcolor="red")
                self.page.snack_bar.open = True
                self.page.update()
            except Exception as ex:
                print(f"Ошибка регистрации: {ex}")

        self.page.add(
            ft.Container(
                content=ft.Column([
                    ft.Text("Регистрация", size=35, weight="bold", color=MY_ACCENT),
                    avatar_container,
                    ft.TextButton("Выбрать фото", on_click=lambda _: self.reg_avatar_picker.pick_files()),
                    reg_login,
                    reg_email,
                    reg_pass,
                    ft.Container(height=10),
                    ft.ElevatedButton("Создать аккаунт", on_click=register_click, bgcolor=MY_ACCENT,
                                      color=SIDEBAR_COLOR, width=300, height=45),
                    ft.TextButton("Назад", on_click=lambda _: self.show_auth_screen())
                ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=10,
                    scroll=ft.ScrollMode.AUTO),
                expand=True,
                alignment=ft.alignment.center
            )
        )

    def on_avatar_selected(self, e: ft.FilePickerResultEvent):
        if e.files:
            try:
                with open(e.files[0].path, "rb") as f:
                    # Читаем байты файла
                    self.temp_avatar_bytes = f.read()

                # Сразу обновляем страницу профиля, чтобы увидеть превью
                self.show_profile_view()
                self.page.update()
            except Exception as ex:
                print(f"Ошибка при выборе файла: {ex}")

    # Измените эти методы в вашем коде
    def safe_navigate(self, destination_func, *args):
        # Сохраняем аргументы, чтобы использовать их после подтверждения
        self.pending_transition = lambda: destination_func(*args)

        if self.temp_avatar_bytes is not None:
            confirm_dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text("Несохраненные изменения"),
                content=ft.Text("Вы изменили аватар, но не сохранили его. Выйти без сохранения?"),
                actions=[
                    ft.TextButton("Да", on_click=lambda _: self.confirm_exit()),
                    ft.TextButton("Нет", on_click=lambda _: self.close_dialog()),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
            self.page.overlay.append(confirm_dialog)
            confirm_dialog.open = True
            self.page.update()
        else:
            # Если изменений нет, вызываем сохраненную лямбду
            self.pending_transition()

    def confirm_exit(self):
        self.temp_avatar_bytes = None
        self.close_dialog()  # Используем ваш метод для закрытия
        if hasattr(self, 'pending_transition'):
            self.pending_transition()

    def on_reg_avatar_selected(self, e: ft.FilePickerResultEvent):
        if e.files:
            with open(e.files[0].path, "rb") as f:
                self.temp_reg_avatar = image_to_base64(f.read())
            # Перерисовываем экран регистрации, чтобы превью обновилось
            self.show_registration_screen()

    # --- ГЛАВНОЕ ОКНО ---
    def show_main_app(self):
        self.page.clean()

        # Создаем сайдбар через новый метод
        sidebar = self.build_sidebar()

        # Основной ряд приложения
        self.page.add(
            ft.Container(
                content=ft.Column([
                    ft.Row([
                        sidebar,  # Вот он!
                        ft.VerticalDivider(width=1, color="#282828"),
                        self.main_view_container  # Здесь Главная / Плейлисты
                    ], expand=True),
                    self.player_controls  # Нижний плеер
                ]),
                expand=True,
                bgcolor=BG_COLOR
            )
        )
        self.load_main_view()

    def load_user_data(self):
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT email, avatar_path, password FROM user WHERE id = ?", (self.user_id,)).fetchone()
        conn.close()
        self.email, self.avatar_path, self.password = (row[0] or "", row[1], row[2]) if row else ("", None, "")

    def init_ui(self):
        # Заголовок трека
        self.track_title = ft.Text(
            "Не выбран",
            weight="bold",
            size=18,
            overflow=ft.TextOverflow.ELLIPSIS,
            max_lines=1
        )
        self.track_artist = ft.Text(
            "",
            size=14,
            color=TEXT_SUB,
            overflow=ft.TextOverflow.ELLIPSIS,
            max_lines=1
        )

        self.play_btn = ft.IconButton(
            ft.Icons.PLAY_CIRCLE_FILLED,
            icon_size=65,
            icon_color=MY_ACCENT,
            on_click=self.toggle_play
        )

        self.progress_slider = ft.Slider(
            min=0, max=100, value=0, active_color=MY_ACCENT,
            on_change_start=self.on_seek_start,
            on_change_end=self.seek_audio
        )

        self.volume_slider = ft.Slider(
            min=0, max=100, value=25, width=120, active_color=MY_ACCENT,
            on_change=self.change_volume
        )

        self.time_info = ft.Text("0:00 / 0:00", size=12, color=TEXT_SUB)
        self.mute_btn = ft.IconButton(ft.Icons.VOLUME_UP_ROUNDED, on_click=self.toggle_mute)
        self.player_cover = ft.Container(
            width=75, height=75, bgcolor="#282828", border_radius=8,
            content=ft.Icon(ft.Icons.MUSIC_NOTE)
        )

        self.main_view_container = ft.Container(expand=True)
        self.tracks_list_view = ft.ListView(expand=True, spacing=0)

        self.shuffle_btn = ft.IconButton(
            ft.Icons.SHUFFLE,
            icon_color=TEXT_SUB,
            on_click=self.toggle_shuffle,
            tooltip="Перемешать"
        )

        # Сохраняем панель плеера в self.player_controls, чтобы show_main_app её видел
        self.player_controls = ft.Container(
            height=170, bgcolor="#181818", border_radius=20, margin=15, padding=20,
            content=ft.Column([
                self.progress_slider,
                ft.Row([
                    ft.Row([
                        self.player_cover,
                        ft.Container(
                            content=ft.Column([self.track_title, self.track_artist], spacing=2),
                            width=250
                        )
                    ], width=350),
                    ft.Row([
                        self.shuffle_btn,
                        ft.IconButton(ft.Icons.SKIP_PREVIOUS, on_click=lambda _: self.play_next(-1)),
                        self.play_btn,
                        ft.IconButton(ft.Icons.SKIP_NEXT, on_click=lambda _: self.play_next(1))
                    ], expand=True, alignment="center"),
                    ft.Row([self.time_info, self.mute_btn, self.volume_slider], width=350, alignment="end")
                ])
            ])
        )

    def load_main_view(self):
        self.search_field.value = ""
        self.search_field.suffix.visible = False
        self.search_query = ""  # Сбрасываем переменную запроса

        self.del_confirm_btn = ft.ElevatedButton(
            "Удалить выбранные", bgcolor="red", color="white", visible=False,
            on_click=self.delete_selected_tracks
        )

        # ИСПРАВЛЕННАЯ ШАПКА ТАБЛИЦЫ
        table_header = ft.Container(
            padding=ft.padding.only(left=20, right=60, bottom=10),  # Увеличили правый отступ для "трех точек"
            border=ft.border.only(bottom=ft.BorderSide(1, "#282828")),
            content=ft.Row([
                ft.Text("#", width=30, color=TEXT_SUB),
                ft.Text("Название", expand=4, color=TEXT_SUB),
                ft.Text("Альбом", expand=3, color=TEXT_SUB),
                # Иконка времени выровнена по правому краю своего блока
                ft.Container(
                    content=ft.Icon(ft.Icons.ACCESS_TIME, size=16, color=TEXT_SUB),
                    width=50,
                    alignment=ft.alignment.center_right
                ),
                # Для колонки с меню (три точки) в шапке ничего не пишем,
                # но за счет правого padding в Container место зарезервировано
            ])
        )

        self.main_view_container.content = ft.Container(
            padding=30,
            content=ft.Column(controls=[
                ft.Row([
                    ft.Column([
                        ft.Text("Главная", size=28, weight="bold"),
                        self.search_field,
                    ], spacing=10),
                    ft.Row([
                        self.del_confirm_btn,
                        ft.IconButton(
                            ft.Icons.ADD,
                            tooltip="Добавить треки",
                            on_click=lambda _: self.track_picker.pick_files(allow_multiple=True)
                        )
                    ])
                ], alignment="spaceBetween", vertical_alignment="start"),
                ft.Container(height=20),
                table_header,
                self.tracks_list_view
            ])
        )
        self.page.update()
        self.refresh_grid()

    def refresh_grid(self):
        # 1. Очищаем список
        self.tracks_list_view.controls = []
        self.track_controls = {}

        conn = sqlite3.connect(DB_PATH)
        query = "SELECT DISTINCT id, title, artist, cover_data, duration, album FROM track"
        self.all_tracks = conn.execute(query).fetchall()
        conn.close()

        # 2. Фильтрация поиска
        search_text = self.search_query.lower().strip()
        filtered_tracks = [
            t for t in self.all_tracks
            if search_text in (t[1] or "").lower() or search_text in (t[2] or "").lower()
        ]

        # 3. Наполнение списка через вызов ОБЩЕГО метода
        for index, (tid, title, artist, cover_bytes, duration, album) in enumerate(filtered_tracks, start=1):
            # ВЫЗЫВАЕМ ТОТ САМЫЙ МЕТОД С ТРЕМЯ ТОЧКАМИ
            track_row = self.create_track_row(index, tid, title, artist, cover_bytes, duration, album)
            self.tracks_list_view.controls.append(track_row)

        self.page.update()

    def update_sidebar_playlists(self):
        # Очищаем колонку в сайдбаре
        self.playlists_column.controls.clear()

        conn = sqlite3.connect(DB_PATH)
        # Обязательно фильтруем по current user_id!
        playlists = conn.execute(
            "SELECT id, name, cover_data FROM playlist WHERE user_id=?",
            (self.user_id,)
        ).fetchall()
        conn.close()

        for pid, name, cover in playlists:
            cover_b64 = image_to_base64(cover)
            self.playlists_column.controls.append(
                ft.ListTile(
                    leading=ft.Container(
                        width=30, height=30, border_radius=3,
                        content=ft.Image(src_base64=cover_b64, fit="cover") if cover_b64 else ft.Icon(ft.Icons.ALBUM,
                                                                                                      size=20)
                    ),
                    title=ft.Text(name, color=TEXT_SUB, size=14, overflow="ellipsis"),
                    on_click=lambda e, p_id=pid: self.safe_navigate(self.load_playlist_view, p_id)
                )
            )
        self.page.update()

    def load_playlist_view(self, playlist_id):
        # 1. Запоминаем текущий ID и сбрасываем поиск
        self.current_playlist_id = playlist_id
        if hasattr(self, "playlist_search_field"):
            self.playlist_search_field.value = ""

        # 2. Получаем данные о плейлисте и его треках из БД
        conn = sqlite3.connect(DB_PATH)
        # Важно: убедись, что таблица называется playlist или playlists (в твоем коде playlist)
        playlist_info = conn.execute("SELECT name, cover_data FROM playlist WHERE id=?", (playlist_id,)).fetchone()

        # Запрос треков через таблицу связей playlist_track
        query = """
                SELECT t.id, t.title, t.artist, t.cover_data, t.duration, t.album
                FROM track t
                         JOIN playlist_track pt ON t.id = pt.track_id
                WHERE pt.playlist_id = ?
                """
        playlist_tracks = conn.execute(query, (playlist_id,)).fetchall()
        conn.close()

        if not playlist_info:
            return

        name, cover = playlist_info
        # Используем твою функцию image_to_base64
        cover_b64 = image_to_base64(cover)

        # 3. ПОДГОТОВКА: Очищаем старые ссылки и сохраняем новые данные
        self.track_rows.clear()  # ВАЖНО для стабильности
        self.all_tracks = playlist_tracks  # Сохраняем для фильтрации поиском
        self.tracks_list_view.controls.clear()

        # 4. Создаем "шапку" плейлиста
        header = ft.Row([
            ft.Row([
                ft.Container(
                    width=150, height=150, border_radius=10,
                    content=ft.Image(src_base64=cover_b64, fit="cover") if cover_b64 else ft.Icon(ft.Icons.ALBUM,
                                                                                                  size=80)
                ),
                ft.Column([
                    ft.Text("ПЛЕЙЛИСТ", size=12, weight="bold", color=TEXT_SUB),
                    ft.Text(name, size=45, weight="bold"),
                    ft.Text(f"Треков: {len(playlist_tracks)}", color=TEXT_SUB)
                ], spacing=5)
            ], expand=True),

            # Кнопка удаления плейлиста
            ft.IconButton(
                icon=ft.Icons.DELETE_FOREVER_OUTLINED,
                icon_color="red",
                tooltip="Удалить плейлист",
                on_click=lambda _: self.delete_playlist(playlist_id)
            )
        ], spacing=20, alignment="spaceBetween")

        # 5. Наполняем список треками
        for index, (tid, title, artist, cover_bytes, duration, album) in enumerate(playlist_tracks, start=1):
            # Передаем playlist_id, чтобы внутри строки появилась кнопка "Удалить из плейлиста"
            track_row = self.create_track_row(index, tid, title, artist, cover_bytes, duration, album,
                                              playlist_id=playlist_id)

            # Сохраняем ссылку на строку в словарь для подсветки плеером
            self.track_rows[tid] = track_row
            self.tracks_list_view.controls.append(track_row)

        # 6. Формируем финальный вид с ПОИСКОМ
        self.main_view_container.content = ft.Container(
            padding=30,
            content=ft.Column([
                header,
                ft.Container(height=10),
                # Добавляем поле поиска (если оно создано в __init__)
                self.playlist_search_field if hasattr(self, "playlist_search_field") else ft.Container(),
                ft.Divider(color="#282828"),
                self.tracks_list_view
            ], scroll=ft.ScrollMode.ADAPTIVE)
        )

        self.page.update()

    def on_playlist_search_change(self, e):

        if e.control.page:
            e.control.suffix.visible = True if e.control.value else False
            e.control.update()

        search_text = e.control.value.lower().strip()
        self.tracks_list_view.controls = []

        # Фильтруем сохраненный в load_playlist_view список all_tracks
        for index, track in enumerate(self.all_tracks, start=1):
            tid, title, artist = track[0], track[1], track[2]
            if search_text in title.lower() or search_text in artist.lower():
                row = self.create_track_row(index, tid, title, artist, track[3], track[4], track[5],
                                            playlist_id=self.current_playlist_id)
                self.tracks_list_view.controls.append(row)
        self.page.update()

    def create_track_row(self, index, tid, title, artist, cover_bytes, duration, album, playlist_id=None):
        # 1. Проверяем, играет ли этот конкретный трек прямо сейчас
        is_playing = (tid == self.current_track_id)
        cover_b64 = image_to_base64(cover_bytes)

        # 2. Определяем, что показать слева: номер или анимацию APNG
        if is_playing:
            apng_path = resource_path("icon.apng")
            if os.path.exists(apng_path):
                # Если файл найден, используем его как src (это заставит APNG работать)
                leading_widget = ft.Image(src=apng_path, width=20, height=20, fit="contain")
            else:
                # Если файла нет, используем иконку Play как запасной вариант
                leading_widget = ft.Icon(ft.Icons.PLAY_ARROW, color=MY_ACCENT, size=20)
        else:
            # Если трек не играет, просто показываем его номер
            leading_widget = ft.Text(str(index), color=TEXT_SUB, size=14)

        # Формируем список пунктов меню
        menu_items = []
        if playlist_id:
            menu_items.append(
                ft.PopupMenuItem(
                    text="Удалить из плейлиста",
                    icon=ft.Icons.PLAYLIST_REMOVE,
                    on_click=lambda _: self.delete_track_from_playlist(playlist_id, tid)
                )
            )
        else:
            menu_items.append(
                ft.PopupMenuItem(
                    text="Добавить в плейлист",
                    icon=ft.Icons.PLAYLIST_ADD,
                    on_click=lambda _: self.show_add_to_playlist_dialog(tid)
                )
            )
            menu_items.append(
                ft.PopupMenuItem(
                    text="Удалить из базы",
                    icon=ft.Icons.DELETE_OUTLINE,
                    on_click=lambda _: self.delete_single_track(tid)
                )
            )

        item_menu = ft.PopupMenuButton(
            icon=ft.Icons.MORE_VERT,
            icon_color=TEXT_SUB,
            items=menu_items
        )

        row = ft.Container(
            data=str(index),
            padding=10, border_radius=8,
            on_hover=self.on_track_hover,
            on_click=lambda e, t_id=tid, t=title, a=artist: self.play_audio(t_id, t, a),
            content=ft.Row([
                ft.Container(leading_widget, width=30, alignment=ft.alignment.center),
                ft.Row([
                    ft.Container(
                        width=40, height=40, border_radius=4,
                        content=ft.Image(src_base64=cover_b64, fit="cover") if cover_b64 else ft.Icon(
                            ft.Icons.MUSIC_NOTE, size=20)
                    ),
                    ft.Column([
                        # Текст заголовка будет синим (MY_ACCENT), если трек играет
                        ft.Text(title or "Без названия", weight="bold",
                                color=MY_ACCENT if is_playing else "white",
                                size=14, overflow="ellipsis"),
                        ft.Text(artist or "Неизвестен", color=TEXT_SUB, size=12, overflow="ellipsis"),
                    ], spacing=0, expand=True)
                ], expand=4),
                ft.Text(album or "Сингл", expand=3, color=TEXT_SUB, size=13),
                ft.Text(duration or "--:--", color=TEXT_SUB, size=13, width=50, text_align="right"),
                item_menu
            ])
        )

        # Сохраняем ссылку на строку в словаре (важно для переключения треков)
        self.track_controls[tid] = row
        # Также сохраняем в track_rows на случай, если логика плейлиста использует этот словарь
        self.track_rows[tid] = row

        return row

    def delete_playlist(self, playlist_id):
        def confirm_delete(e):
            try:
                conn = sqlite3.connect(DB_PATH)
                # Удаляем сам плейлист (связи в playlist_track удалятся сами благодаря ON DELETE CASCADE)
                conn.execute("DELETE FROM playlist WHERE id=?", (playlist_id,))
                conn.commit()
                conn.close()

                dialog.open = False
                self.update_sidebar_playlists()  # Обновляем список слева
                self.load_main_view()  # Переходим на главную

                self.page.snack_bar = ft.SnackBar(ft.Text("Плейлист удален"), bgcolor="red")
                self.page.snack_bar.open = True
                self.page.update()
            except Exception as ex:
                print(f"Ошибка удаления плейлиста: {ex}")

        dialog = ft.AlertDialog(
            title=ft.Text("Удаление плейлиста"),
            content=ft.Text("Вы уверены, что хотите удалить этот плейлист?"),
            actions=[
                ft.TextButton("Отмена", on_click=lambda _: self.close_dialog()),
                ft.ElevatedButton("Удалить", on_click=confirm_delete, bgcolor="red", color="white"),
            ]
        )
        self.page.overlay.append(dialog)
        dialog.open = True
        self.page.update()

    def delete_track_from_playlist(self, playlist_id, track_id):
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("DELETE FROM playlist_track WHERE playlist_id=? AND track_id=?", (playlist_id, track_id))
            conn.commit()
            conn.close()

            # Перерисовываем текущий вид плейлиста, чтобы трек исчез
            self.load_playlist_view(playlist_id)

            self.page.snack_bar = ft.SnackBar(ft.Text("Трек удален из плейлиста"), bgcolor="blue")
            self.page.snack_bar.open = True
            self.page.update()
        except Exception as ex:
            print(f"Ошибка при удалении из плейлиста: {ex}")

    def show_add_to_playlist_dialog(self, track_id):
        conn = sqlite3.connect(DB_PATH)
        playlists = conn.execute("SELECT id, name FROM playlist WHERE user_id=?", (self.user_id,)).fetchall()
        conn.close()

        if not playlists:
            self.page.snack_bar = ft.SnackBar(ft.Text("У вас еще нет плейлистов!"), bgcolor="orange")
            self.page.snack_bar.open = True
            self.page.update()
            return

        def add_to_existing(p_id):
            try:
                conn = sqlite3.connect(DB_PATH)
                conn.execute("INSERT INTO playlist_track (playlist_id, track_id) VALUES (?, ?)", (p_id, track_id))
                conn.commit()
                conn.close()
                dialog.open = False
                self.page.snack_bar = ft.SnackBar(ft.Text("Добавлено в плейлист!"), bgcolor="green")
                self.page.update()
            except sqlite3.IntegrityError:
                self.page.snack_bar = ft.SnackBar(ft.Text("Этот трек уже есть в плейлисте"), bgcolor="orange")
                self.page.update()

        # ИСПОЛЬЗУЕМ height ДЛЯ ОГРАНИЧЕНИЯ ВЫСОТЫ
        playlist_buttons = ft.Container(
            content=ft.Column([
                ft.ListTile(
                    title=ft.Text(name),
                    leading=ft.Icon(ft.Icons.QUEUE_MUSIC),
                    on_click=lambda _, pid=pid: add_to_existing(pid)
                ) for pid, name in playlists
            ], tight=True, scroll=ft.ScrollMode.AUTO),
            height=300,  # Указываем фиксированную высоту
        )

        dialog = ft.AlertDialog(
            title=ft.Text("Выберите плейлист"),
            content=playlist_buttons,
            actions=[ft.TextButton("Отмена", on_click=lambda _: self.close_dialog())]
        )

        self.page.overlay.append(dialog)
        dialog.open = True
        self.page.update()

    def delete_single_track(self, track_id):
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM track WHERE id=?", (track_id,))
        conn.commit()
        conn.close()
        self.refresh_grid()

    def on_track_hover(self, e):
        """Метод для эффекта наведения с защитой от ошибок при удалении"""
        # Проверяем, привязан ли контроль к странице, прежде чем обновлять
        if e.control.page:
            e.control.bgcolor = "#2A2A2A" if e.data == "true" else None
            try:
                e.control.update()
            except:
                # Если элемент успел исчезнуть в момент обновления, просто игнорируем
                pass

    def on_track_selected(self, e):
        """Логика выбора чекбокса для удаления"""
        tid = e.control.parent.parent.parent.data
        if e.control.value:
            self.selected_tracks.add(tid)
        else:
            self.selected_tracks.discard(tid)

    def load_assets(self):
        """Загрузка системной гифки из базы данных"""
        try:
            conn = sqlite3.connect(DB_PATH)
            row = conn.execute("SELECT data FROM resources WHERE id='equalizer'").fetchone()
            conn.close()
            if row:
                self.equalizer_b64 = base64.b64encode(row[0]).decode()
        except Exception as e:
            print(f"Ошибка загрузки ассетов: {e}")

    def show_profile_view(self):
        self.load_user_data()

        # 1. Создаем поля ввода (теперь они определены!)
        prof_email = ft.TextField(label="Email", value=self.email, width=400, border_color=MY_ACCENT)
        prof_pass = ft.TextField(label="Новый пароль", width=400, password=True, border_color=MY_ACCENT)

        # 2. Логика отображения аватара (текущий или временный)
        display_image = None
        if self.temp_avatar_bytes:
            # Превью еще не сохраненного фото
            display_image = ft.Image(src_base64=base64.b64encode(self.temp_avatar_bytes).decode(), fit="cover")
        elif self.avatar_path:
            # Фото из базы
            display_image = ft.Image(src_base64=self.avatar_path, fit="cover")

        avatar_ui = ft.Container(
            width=140, height=140, border_radius=70, bgcolor="#222",
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            content=display_image if display_image else ft.Icon(ft.Icons.PERSON, size=50)
        )

        # 3. Функция сохранения
        def save_profile_changes(e):
            try:
                conn = sqlite3.connect(DB_PATH)
                # Если выбрали новое фото - сохраняем его
                if self.temp_avatar_bytes:
                    new_avatar_b64 = base64.b64encode(self.temp_avatar_bytes).decode()
                    conn.execute("UPDATE user SET avatar_path=? WHERE id=?", (new_avatar_b64, self.user_id))
                    self.temp_avatar_bytes = None  # Сбрасываем временный статус

                # Хешируем пароль только если его ввели
                new_p = hash_password(prof_pass.value) if prof_pass.value else self.password

                conn.execute("UPDATE user SET email=?, password=? WHERE id=?",
                             (prof_email.value, new_p, self.user_id))
                conn.commit()
                conn.close()

                # Обновляем данные в приложении
                self.load_user_data()
                # Сразу меняем аватарку в сайдбаре
                if self.avatar_path:
                    self.user_avatar.content = ft.Image(src_base64=self.avatar_path, fit="cover")

                self.page.snack_bar = ft.SnackBar(ft.Text("Изменения сохранены!"), bgcolor="green")
                self.page.snack_bar.open = True
                self.show_profile_view()  # Перерисовываем страницу
                self.page.update()
            except Exception as ex:
                print(f"Ошибка сохранения: {ex}")

        # 4. Собираем UI
        self.main_view_container.content = ft.Container(
            padding=50, alignment=ft.alignment.top_center,
            content=ft.Column([
                ft.Text("Профиль", size=32, weight="bold"),
                avatar_ui,
                ft.TextButton("Изменить фото профиля", on_click=lambda _: self.avatar_picker.pick_files()),
                ft.Text(f"Логин: {self.username}", size=20, color=MY_ACCENT, weight="bold"),
                prof_email,
                prof_pass,
                ft.ElevatedButton("Сохранить изменения", on_click=save_profile_changes, bgcolor=MY_ACCENT,
                                  color=SIDEBAR_COLOR, height=50, width=250),
                ft.OutlinedButton("Выйти из аккаунта", on_click=lambda _: self.logout(), width=250,
                                  style=ft.ButtonStyle(color="red")),
            ], horizontal_alignment="center", spacing=15, scroll=ft.ScrollMode.AUTO)
        )
        self.page.update()

    # --- ЛОГИКА ТРЕКОВ ---
    def on_import_result(self, e: ft.FilePickerResultEvent):
        if not e.files: return
        conn = sqlite3.connect(DB_PATH)
        for f in e.files:
            # По умолчанию значения, если тегов нет
            title, artist, album, cover, duration = os.path.basename(f.path), "Неизвестен", "Сингл", None, "0:00"
            try:
                meta = MutagenFile(f.path)
                if meta:
                    title = str(meta.get('TIT2', [title])[0]) if meta.get('TIT2') else title
                    artist = str(meta.get('TPE1', ["Неизвестен"])[0]) if meta.get('TPE1') else artist
                    # ЧИТАЕМ АЛЬБОМ (Тег TALB)
                    album = str(meta.get('TALB', ["Сингл"])[0]) if meta.get('TALB') else "Сингл"

                    length = int(meta.info.length)
                    mins, secs = divmod(length, 60)
                    duration = f"{mins}:{secs:02}"

                    if hasattr(meta, 'pictures') and meta.pictures:
                        cover = meta.pictures[0].data
                    elif 'APIC:' in meta:
                        cover = meta.get('APIC:').data
            except:
                pass

            with open(f.path, "rb") as bf:
                # Добавляем album в запрос
                conn.execute(
                    "INSERT INTO track (title, artist, album, audio_data, cover_data, duration) VALUES (?, ?, ?, ?, ?, ?)",
                    (title, artist, album, bf.read(), cover, duration))
        conn.commit()
        conn.close()
        self.refresh_grid()

    def play_audio(self, tid, title, artist):
        # --- ВИЗУАЛЬНОЕ ОБНОВЛЕНИЕ СТАРОГО ТРЕКА ---
        # Проверяем оба словаря, где могут лежать ссылки на строки
        for storage in [self.track_rows, self.track_controls]:
            if self.current_track_id in storage:
                old_row = storage[self.current_track_id]
                if old_row.page:  # Проверка, что строка видна на экране
                    try:
                        # 1. Возвращаем белый цвет тексту названия
                        # Структура: Container -> Row -> Row(1) -> Column(1) -> Text(0)
                        old_row.content.controls[1].controls[1].controls[0].color = ft.Colors.WHITE

                        # 2. Возвращаем номер трека вместо иконки/анимации
                        # Мы берем сохраненный индекс из данных строки (если он там есть)
                        track_idx = old_row.data if old_row.data else "0"
                        old_row.content.controls[0].content = ft.Text(str(track_idx), color=TEXT_SUB)

                        old_row.update()
                    except:
                        pass

        # Устанавливаем новый ID
        self.current_track_id = tid

        # --- ВИЗУАЛЬНОЕ ОБНОВЛЕНИЕ НОВОГО ТРЕКА ---
        for storage in [self.track_rows, self.track_controls]:
            if tid in storage:
                new_row = storage[tid]
                if new_row.page:
                    try:
                        # 1. Красим название в синий
                        new_row.content.controls[1].controls[1].controls[0].color = MY_ACCENT

                        # 2. Ставим анимацию APNG (используем resource_path для надежности)
                        # Если файла нет, просто ставим иконку
                        apng_path = resource_path("icon.apng")
                        if os.path.exists(apng_path):
                            new_row.content.controls[0].content = ft.Image(
                                src=apng_path,
                                width=20,
                                height=20,
                                fit=ft.ImageFit.CONTAIN
                            )
                        else:
                            new_row.content.controls[0].content = ft.Icon(ft.Icons.PLAY_ARROW, color=MY_ACCENT, size=20)

                        new_row.update()
                    except:
                        pass

        # --- ДАЛЕЕ ТВОЙ СТАНДАРТНЫЙ КОД ВОСПРОИЗВЕДЕНИЯ ---
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT audio_data, cover_data FROM track WHERE id=?", (tid,)).fetchone()
        conn.close()
        if not row: return
        audio_data, cover_bytes = row

        if self.audio:
            try:
                self.audio.pause()
                self.audio.release()
                if self.audio in self.page.overlay:
                    self.page.overlay.remove(self.audio)
            except:
                pass

        if self.current_temp_path and os.path.exists(self.current_temp_path):
            try:
                os.remove(self.current_temp_path)
            except:
                pass

        fd, temp_path = tempfile.mkstemp(suffix=".mp3")
        with os.fdopen(fd, 'wb') as tmp:
            tmp.write(audio_data)
        self.current_temp_path = temp_path

        self.track_title.value = title or "Без названия"
        self.track_artist.value = artist or "Неизвестен"
        cover_b64 = image_to_base64(cover_bytes)
        self.player_cover.content = ft.Image(src_base64=cover_b64, fit="cover") if cover_b64 else ft.Icon(
            ft.Icons.MUSIC_NOTE)

        self.audio = ft_audio.Audio(
            src=self.current_temp_path,
            autoplay=True,
            volume=self.volume_slider.value / 100,
            on_position_changed=lambda e: self.update_progr(int(e.data)),
            on_state_changed=self.handle_audio_state
        )
        self.page.overlay.append(self.audio)
        self.play_btn.icon = ft.Icons.PAUSE_CIRCLE_FILLED
        self.page.update()


    def handle_audio_state(self, e):
        if e.data == "completed":
            self.play_next(1)

    def update_progr(self, pos):
        if not self.audio or self.is_seeking:
            return

        # Попытка получить длительность, если она еще 0
        if self.track_duration <= 0:
            try:
                dur = self.audio.get_duration()
                if dur and dur > 0:
                    self.track_duration = dur
            except:
                pass

        if self.track_duration > 0:
            # Обновляем ползунок (процент 0..100)
            self.progress_slider.value = (pos / self.track_duration) * 100

            # Форматируем время
            cur_min, cur_sec = divmod(int(pos // 1000), 60)
            dur_min, dur_sec = divmod(int(self.track_duration // 1000), 60)
            self.time_info.value = f"{cur_min}:{cur_sec:02} / {dur_min}:{dur_sec:02}"
        else:
            self.time_info.value = "Загрузка..."

        self.page.update()

    def on_seek_start(self, e):
        self.is_seeking = True

    def seek_audio(self, e):
        if not self.audio or self.track_duration <= 0:
            self.is_seeking = False
            return

        # e.control.value — это процент (0..100)
        target_ms = int((e.control.value / 100) * self.track_duration)
        try:
            self.audio.seek(target_ms)
        except Exception as ex:
            print(f"[Seek error] {ex}")

        self.is_seeking = False
        self.page.update()

    def toggle_play(self, e):
        if not self.audio: return
        if self.play_btn.icon == ft.Icons.PLAY_CIRCLE_FILLED:
            self.audio.resume()
            self.play_btn.icon = ft.Icons.PAUSE_CIRCLE_FILLED
        else:
            self.audio.pause()
            self.play_btn.icon = ft.Icons.PLAY_CIRCLE_FILLED
        self.page.update()

    def toggle_shuffle(self, e):
        self.is_shuffle = not self.is_shuffle
        self.shuffle_btn.icon_color = MY_ACCENT if self.is_shuffle else TEXT_SUB

        if self.is_shuffle:
            # Создаем перемешанную копию текущего списка треков
            self.shuffled_list = list(self.all_tracks)
            import random
            random.shuffle(self.shuffled_list)

        self.page.update()

    def play_next(self, delta):
        # Выбираем, в каком списке искать: в обычном или перемешанном
        active_list = self.shuffled_list if self.is_shuffle else self.all_tracks

        if not active_list:
            return

        current_index = -1
        for i, t in enumerate(active_list):
            if t[0] == self.current_track_id:
                current_index = i
                break

        if current_index == -1:
            next_index = 0
        else:
            # Циклическое переключение
            next_index = (current_index + delta) % len(active_list)

        track = active_list[next_index]
        self.play_audio(track[0], track[1], track[2])

    def on_audio_state_changed(self, e):
        if e.data == "completed":
            self.play_next(1)  # Это теперь учитывает self.is_shuffle

        if e.data == "playing":
            self.play_btn.icon = ft.Icons.PAUSE_CIRCLE_FILLED
        else:
            self.play_btn.icon = ft.Icons.PLAY_CIRCLE_FILLED
        self.page.update()

    def change_volume(self, e):
        volume = e.control.value
        if self.audio:
            self.audio.volume = volume / 100
            self.audio.update()

        # Если громкость 0 — ставим иконку MUTE, если больше 0 — обычную
        if volume == 0:
            self.mute_btn.icon = ft.Icons.VOLUME_OFF
            self.is_muted = True
        else:
            self.mute_btn.icon = ft.Icons.VOLUME_UP
            self.is_muted = False

        self.mute_btn.update()

    def toggle_mute(self, e):
        if not self.audio: return

        if not self.is_muted:
            # Выключаем звук
            self.last_volume = self.volume_slider.value
            self.audio.volume = 0
            self.volume_slider.value = 0
            self.mute_btn.icon = ft.Icons.VOLUME_OFF
            self.is_muted = True
        else:
            # Включаем звук (возвращаем к прошлому значению или на 25%)
            restore_vol = self.last_volume if self.last_volume > 0 else 25
            self.audio.volume = restore_vol / 100
            self.volume_slider.value = restore_vol
            self.mute_btn.icon = ft.Icons.VOLUME_UP
            self.is_muted = False

        self.audio.update()
        self.volume_slider.update()
        self.mute_btn.update()

    def handle_track_click(self, tid, cb, title, artist):
        if self.delete_mode:
            cb.value = not cb.value
            if cb.value:
                self.selected_tracks.add(tid)
            else:
                self.selected_tracks.discard(tid)
            self.page.update()
        else:
            self.play_audio(tid, title, artist)

    def delete_selected_tracks(self, e):
        conn = sqlite3.connect(DB_PATH)
        for tid in self.selected_tracks: conn.execute("DELETE FROM track WHERE id=?", (tid,))
        conn.commit()
        conn.close()
        self.delete_mode = False
        self.del_confirm_btn.visible = False
        self.refresh_grid()

    def logout(self):
        if self.audio:
            try:
                self.audio.pause()
                self.audio.release()
                if self.audio in self.page.overlay:
                    self.page.overlay.remove(self.audio)
            except:
                pass
            finally:
                self.audio = None

        # Удаляем файл при выходе
        if self.current_temp_path and os.path.exists(self.current_temp_path):
            try:
                os.remove(self.current_temp_path)
                self.current_temp_path = None
            except:
                pass

        self.current_track_id = None
        self.track_duration = 0
        self.play_btn.icon = ft.Icons.PLAY_CIRCLE_FILLED
        self.progress_slider.value = 0
        self.time_info.value = "0:00 / 0:00"
        self.player_cover.content = ft.Icon(ft.Icons.MUSIC_NOTE)
        self.page.update()
        self.show_auth_screen()


def main(page: ft.Page):
    page.title = "SoundFlow"

    page.window.width = 1200
    page.window.height = 900

    # Минимальные размеры (чтобы дизайн не "ломался")
    page.window.min_width = 700
    page.window.min_height = 850

    init_db()
    SoundFlowApp(page)


if __name__ == "__main__":
    init_db()

    # try:
    #     upload_system_gif("icon.apng")
    # except FileNotFoundError:
    #     print("Предупреждение: Файл icon.gif не найден. Пропустите этот шаг, если гифка уже в базе.")

    ft.app(target=main)
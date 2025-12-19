import flet as ft
import flet_audio as ft_audio
import sqlite3
import os
import tempfile
import base64
from mutagen import File as MutagenFile

# --- Константы стиля ---
BG_COLOR = "#121212"
SIDEBAR_COLOR = "#000000"
CARD_COLOR = "#181818"
MY_ACCENT = "#8bb7f0"
TEXT_SUB = "#B3B3B3"
DB_PATH = "music_app.db"


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


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS user (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE, email TEXT, password TEXT, avatar_path TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS track (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT, artist TEXT, audio_data BLOB, cover_data BLOB, duration TEXT
    )""")
    # Таблица для системных файлов (твоя гифка)
    conn.execute("CREATE TABLE IF NOT EXISTS resources (id TEXT PRIMARY KEY, data BLOB)")

    cursor = conn.execute("PRAGMA table_info(track)")
    columns = [column[1] for column in cursor.fetchall()]
    if "album" not in columns:
        conn.execute("ALTER TABLE track ADD COLUMN album TEXT")

    # Проверка наличия колонки, чтобы не было ошибки при повторном запуске
    cursor = conn.execute("PRAGMA table_info(track)")
    columns = [column[1] for column in cursor.fetchall()]
    if "equalizer_gif" not in columns:
        conn.execute("ALTER TABLE track ADD COLUMN equalizer_gif BLOB")

    conn.commit()
    conn.close()


class SoundFlowApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.user_id = None
        self.username = ""
        self.audio = None
        self.track_controls = {}
        self.all_tracks = []
        self.current_track_id = None
        self.equalizer_b64 = None
        self.load_assets()
        self.delete_mode = False
        self.selected_tracks = set()
        self.last_volume = 25
        self.is_muted = False
        self.temp_reg_avatar = None
        self.track_duration = 0
        self.is_seeking = False  # Флаг, чтобы прогресс не прыгал, когда мы тянем ползунок

        self.page.bgcolor = BG_COLOR
        self.page.theme_mode = ft.ThemeMode.DARK
        self.page.padding = 0

        self.track_picker = ft.FilePicker(on_result=self.on_import_result)
        self.avatar_picker = ft.FilePicker(on_result=self.on_avatar_selected)
        self.reg_avatar_picker = ft.FilePicker(on_result=self.on_reg_avatar_selected)
        self.page.overlay.extend([self.track_picker, self.avatar_picker, self.reg_avatar_picker])

        self.show_auth_screen()

    # --- АВТОРИЗАЦИЯ ---
    def show_auth_screen(self):
        self.page.clean()
        self.user_id = None
        login_in = ft.TextField(label="Логин", width=300, border_color=MY_ACCENT)
        pass_in = ft.TextField(label="Пароль", width=300, password=True, can_reveal_password=True,
                               border_color=MY_ACCENT)

        def login_click(e):
            conn = sqlite3.connect(DB_PATH)
            user = conn.execute("SELECT id, username FROM user WHERE username=? AND password=?",
                                (login_in.value, pass_in.value)).fetchone()
            conn.close()
            if user:
                self.user_id, self.username = user[0], user[1]
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

    def show_registration_screen(self):
        self.page.clean()
        reg_login = ft.TextField(label="Логин", width=300, border_color=MY_ACCENT)
        reg_email = ft.TextField(label="Email", width=300, border_color=MY_ACCENT)
        reg_pass = ft.TextField(label="Пароль", width=300, password=True, border_color=MY_ACCENT)

        # Контейнер для превью аватара
        avatar_container = ft.Container(
            width=100, height=100, border_radius=50, bgcolor="#222",
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            content=ft.Image(src_base64=self.temp_reg_avatar, fit=ft.ImageFit.COVER) if self.temp_reg_avatar
            else ft.Icon(ft.Icons.PERSON, size=40, color=TEXT_SUB)
        )

        def register_click(e):
            if not reg_login.value: return
            conn = sqlite3.connect(DB_PATH)
            conn.execute("INSERT INTO user (username, password, email, avatar_path) VALUES (?, ?, ?, ?)",
                         (reg_login.value, reg_pass.value, reg_email.value, self.temp_reg_avatar))
            conn.commit()
            conn.close()
            self.show_auth_screen()

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
                scroll=ft.ScrollMode.AUTO), # Скролл на случай маленького экрана
                expand=True,
                alignment=ft.alignment.center
            )
        )

    def on_avatar_selected(self, e: ft.FilePickerResultEvent):
        if e.files:
            with open(e.files[0].path, "rb") as f:
                # Преобразуем в base64
                self.avatar_path = image_to_base64(f.read())

            # Сразу сохраняем в базу данных, чтобы не потерять
            conn = sqlite3.connect(DB_PATH)
            conn.execute("UPDATE user SET avatar_path=? WHERE id=?", (self.avatar_path, self.user_id))
            conn.commit()
            conn.close()

            # Перерисовываем профиль, чтобы увидеть новую фотку
            self.show_profile_view()

            self.page.snack_bar = ft.SnackBar(ft.Text("Аватар обновлен!"), bgcolor=MY_ACCENT)
            self.page.snack_bar.open = True
            self.page.update()

    def on_reg_avatar_selected(self, e: ft.FilePickerResultEvent):
        if e.files:
            with open(e.files[0].path, "rb") as f:
                self.temp_reg_avatar = image_to_base64(f.read())
            # Перерисовываем экран регистрации, чтобы превью обновилось
            self.show_registration_screen()

    # --- ГЛАВНОЕ ОКНО ---
    def show_main_app(self):
        self.page.clean()
        self.load_user_data()
        self.init_ui()
        self.load_main_view()

    def load_user_data(self):
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT email, avatar_path, password FROM user WHERE id = ?", (self.user_id,)).fetchone()
        conn.close()
        self.email, self.avatar_path, self.password = (row[0] or "", row[1], row[2]) if row else ("", None, "")

    def init_ui(self):
        # Заголовок трека с ограничением ширины и многоточием
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
        # ИСПОЛЬЗУЕМ ListView для плавной работы
        self.tracks_list_view = ft.ListView(expand=True, spacing=0)

        sidebar = ft.Container(
            width=250, bgcolor=SIDEBAR_COLOR, padding=20,
            content=ft.Column([
                ft.Text("SoundFlow", size=32, weight="bold", color=MY_ACCENT),
                ft.ListTile(leading=ft.Icon(ft.Icons.HOME), title=ft.Text("Главная"),
                            on_click=lambda _: self.load_main_view()),
                ft.ListTile(leading=ft.Icon(ft.Icons.PERSON), title=ft.Text("Профиль"),
                            on_click=lambda _: self.show_profile_view()),
            ])
        )

        player_bar = ft.Container(
            height=170, bgcolor="#181818", border_radius=20, margin=15, padding=20,
            content=ft.Column([
                self.progress_slider,
                ft.Row([
                    # Левая часть: обложка и текст (ограничена по ширине)
                    ft.Row([
                        self.player_cover,
                        ft.Container(
                            content=ft.Column([self.track_title, self.track_artist], spacing=2),
                            width=250  # Фиксируем ширину для срабатывания ELLIPSIS
                        )
                    ], width=350),
                    # Центр: кнопки управления
                    ft.Row([
                        ft.IconButton(ft.Icons.SKIP_PREVIOUS, on_click=lambda _: self.play_next(-1)),
                        self.play_btn,
                        ft.IconButton(ft.Icons.SKIP_NEXT, on_click=lambda _: self.play_next(1))
                    ], expand=True, alignment="center"),
                    # Правая часть: время и громкость
                    ft.Row([self.time_info, self.mute_btn, self.volume_slider], width=350, alignment="end")
                ])
            ])
        )
        self.page.add(ft.Row([sidebar, self.main_view_container], expand=True), player_bar)

    def load_main_view(self):
        # Кнопка удаления
        self.del_confirm_btn = ft.ElevatedButton(
            "Удалить выбранные", bgcolor="red", color="white", visible=False,
            on_click=self.delete_selected_tracks
        )

        # Шапка таблицы
        table_header = ft.Container(
            padding=ft.padding.only(left=20, right=20, bottom=10),
            border=ft.border.only(bottom=ft.BorderSide(1, "#282828")),
            content=ft.Row([
                ft.Text("#", width=30, color=TEXT_SUB),
                ft.Text("Название", expand=4, color=TEXT_SUB),
                ft.Text("Альбом", expand=3, color=TEXT_SUB),
                ft.Container(
                    content=ft.Icon(ft.Icons.ACCESS_TIME, size=16, color=TEXT_SUB),
                    width=50, alignment=ft.alignment.center_right
                ),
            ])
        )

        # Основной контент (Исправлено: используем self.tracks_list_view)
        self.main_view_container.content = ft.Container(
            padding=30,
            content=ft.Column(controls=[
                ft.Row([
                    ft.Text("Главная", size=28, weight="bold"),
                    ft.Row([
                        self.del_confirm_btn,
                        ft.IconButton(ft.Icons.ADD,
                                      on_click=lambda _: self.track_picker.pick_files(allow_multiple=True)),
                        ft.IconButton(ft.Icons.DELETE, on_click=self.toggle_delete_mode)
                    ])
                ], alignment="spaceBetween"),
                ft.Container(height=20),
                table_header,
                self.tracks_list_view  # <-- Здесь была ошибка tracks_column
            ])
        )
        self.refresh_grid()

    def refresh_grid(self):
        self.tracks_list_view.controls.clear()
        self.track_controls = {}

        conn = sqlite3.connect(DB_PATH)
        self.all_tracks = conn.execute("SELECT id, title, artist, cover_data, duration, album FROM track").fetchall()
        conn.close()

        for index, (tid, title, artist, cover_bytes, duration, album) in enumerate(self.all_tracks, start=1):
            is_playing = (tid == self.current_track_id)
            cover_b64 = image_to_base64(cover_bytes)

            # Номер или анимация эквалайзера
            leading_widget = ft.Text(str(index), color=TEXT_SUB, size=14)
            if is_playing and self.equalizer_b64:
                leading_widget = ft.Image(src_base64=self.equalizer_b64, width=20, height=20)

            track_row = ft.Container(
                data=tid,  # Важно для поиска при клике
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
                            ft.Text(title, weight="bold", color=MY_ACCENT if is_playing else "white",
                                    size=14, overflow=ft.TextOverflow.ELLIPSIS, max_lines=1),
                            ft.Text(artist, color=TEXT_SUB, size=12,
                                    overflow=ft.TextOverflow.ELLIPSIS, max_lines=1),
                        ], spacing=0, expand=True)
                    ], expand=4),
                    ft.Text(album if album else "Сингл", expand=3, color=TEXT_SUB,
                            size=13, overflow=ft.TextOverflow.ELLIPSIS, max_lines=1),
                    ft.Row([
                        ft.Checkbox(visible=self.delete_mode, value=(tid in self.selected_tracks),
                                    on_change=self.on_track_selected),
                        ft.Text(duration if duration else "--:--", color=TEXT_SUB, size=13, width=50,
                                text_align="right")
                    ])
                ])
            )
            self.track_controls[tid] = track_row
            self.tracks_list_view.controls.append(track_row)

        self.page.update()

    def on_track_hover(self, e):
        """Метод для эффекта наведения (оптимизирован)"""
        e.control.bgcolor = "#2A2A2A" if e.data == "true" else None
        e.control.update()

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
        self.load_user_data() # Обновляем данные из БД (включая путь к аватарке)
        prof_email = ft.TextField(label="Email", value=self.email, width=400, border_color=MY_ACCENT)
        prof_pass = ft.TextField(label="Новый пароль", width=400, password=True, border_color=MY_ACCENT)

        avatar_ui = ft.Container(
            width=140, height=140, border_radius=70, clip_behavior=ft.ClipBehavior.HARD_EDGE,
            # Здесь теперь всегда актуальная avatar_path
            content=ft.Image(src_base64=self.avatar_path, fit=ft.ImageFit.COVER) if self.avatar_path
            else ft.Icon(ft.Icons.PERSON, size=50, color=TEXT_SUB),
            bgcolor="#222"
        )

        def save_profile_changes(e):
            new_p = prof_pass.value if prof_pass.value else self.password
            conn = sqlite3.connect(DB_PATH)
            conn.execute("UPDATE user SET email=?, password=? WHERE id=?",
                         (prof_email.value, new_p, self.user_id))
            conn.commit()
            conn.close()
            self.page.snack_bar = ft.SnackBar(ft.Text("Данные сохранены"), bgcolor="green")
            self.page.snack_bar.open = True
            self.page.update()

        self.main_view_container.content = ft.Container(
            padding=50, alignment=ft.alignment.top_center,
            content=ft.Column([
                ft.Text("Профиль", size=32, weight="bold"),
                avatar_ui,
                ft.TextButton("Изменить фото профиля", on_click=lambda _: self.avatar_picker.pick_files()),
                ft.Text(f"Логин: {self.username}", size=20, color=MY_ACCENT, weight="bold"),
                prof_email, prof_pass,
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
        # 1. Возвращаем старый трек в обычное состояние
        if self.current_track_id is not None and self.current_track_id in self.track_controls:
            old_row = self.track_controls[self.current_track_id]
            idx = next((i for i, t in enumerate(self.all_tracks) if t[0] == self.current_track_id), -1)
            if idx != -1:
                old_row.content.controls[0].content = ft.Text(str(idx + 1), color=TEXT_SUB, size=14)
            old_row.content.controls[1].controls[1].controls[0].color = "white"
            old_row.update()

        # 2. Подсвечиваем новый трек
        self.current_track_id = tid
        if tid in self.track_controls:
            new_row = self.track_controls[tid]
            if self.equalizer_b64:
                new_row.content.controls[0].content = ft.Image(src_base64=self.equalizer_b64, width=20, height=20)
            new_row.content.controls[1].controls[1].controls[0].color = MY_ACCENT
            new_row.update()

        # 3. Обновляем данные плеера
        self.track_title.value = title or "Без названия"
        self.track_artist.value = artist or "Неизвестен"

        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT audio_data, cover_data FROM track WHERE id=?", (tid,)).fetchone()
        conn.close()

        if not row: return
        audio_data, cover_bytes = row

        cover_b64 = image_to_base64(cover_bytes)
        self.player_cover.content = ft.Image(src_base64=cover_b64, fit="cover") if cover_b64 else ft.Icon(
            ft.Icons.MUSIC_NOTE)

        # 4. Запуск движка воспроизведения
        fd, temp_path = tempfile.mkstemp(suffix=".mp3")
        with os.fdopen(fd, 'wb') as tmp:
            tmp.write(audio_data)

        if self.audio:
            try:
                self.audio.release()
                if self.audio in self.page.overlay:
                    self.page.overlay.remove(self.audio)
            except:
                pass

        self.audio = ft_audio.Audio(
            src=temp_path, autoplay=True,
            volume=self.volume_slider.value / 100,
            on_position_changed=lambda e: self.update_progr(int(e.data)),
            on_state_changed=self.handle_audio_state
        )
        self.page.overlay.append(self.audio)

        self.track_duration = 0
        self.progress_slider.value = 0
        self.is_seeking = False
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

    def play_next(self, step):
        if not hasattr(self, 'all_tracks'): return
        ids = [t[0] for t in self.all_tracks]
        if self.current_track_id in ids:
            idx = (ids.index(self.current_track_id) + step) % len(ids)
            t = self.all_tracks[idx]
            self.play_audio(t[0], t[1], t[2])

    def change_volume(self, e):
        if self.audio:
            self.audio.volume = e.control.value / 100
            self.audio.update()

    def toggle_mute(self, e):
        if not self.audio: return
        if not self.is_muted:
            self.last_volume = self.volume_slider.value
            self.audio.volume = 0
            self.volume_slider.value = 0
            self.mute_btn.icon = ft.Icons.VOLUME_OFF
        else:
            self.audio.volume = self.last_volume / 100
            self.volume_slider.value = self.last_volume
            self.mute_btn.icon = ft.Icons.VOLUME_UP
        self.is_muted = not self.is_muted
        self.page.update()

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

    def toggle_delete_mode(self, e):
        self.delete_mode = not self.delete_mode
        self.del_confirm_btn.visible = self.delete_mode
        self.refresh_grid()

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
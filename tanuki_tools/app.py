from __future__ import annotations

import os
import json
import queue
import sys
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .csv_tools import CsvToolError, discover_scripts, export_dialogues, import_dialogues
from .tac_tools import OperationCancelled, TacArchive, TacError


APP_TITLE = "Tanuki Tools"
SETTINGS_DIRECTORY = "TanukiTools"
SETTINGS_FILENAME = "settings.json"


def settings_path() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".config"
    return base / SETTINGS_DIRECTORY / SETTINGS_FILENAME


def load_settings(path: Path | None = None) -> dict[str, object]:
    source = path or settings_path()
    if not source.is_file():
        return {}
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def find_game_folder() -> Path:
    candidates: list[Path] = []
    for start in (Path.cwd(), Path(sys.executable).resolve().parent, Path(__file__).resolve().parent):
        candidates.extend([start, *start.parents])
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if (candidate / "datapic.tac").is_file() or (candidate / "datascn.tac").exists():
            return candidate
    return Path.cwd()


class TanukiToolsApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1040x780")
        self.minsize(900, 680)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.settings_file = settings_path()
        self.settings = load_settings(self.settings_file)
        self.base_folder = self._last_existing_folder()
        self.script_infos = []
        self.archive: TacArchive | None = None
        self.job_running = False
        self.cancel_event = threading.Event()
        self.events: queue.Queue = queue.Queue()
        self._configure_style()
        self._build_ui()
        self.after(100, self._poll_events)
        if self.csv_source_var.get().strip() and Path(self.csv_source_var.get()).is_dir():
            self.after(250, self.analyse_csv)

    def _saved_path(self, key: str) -> str:
        value = self.settings.get(key, "")
        return value if isinstance(value, str) else ""

    def _last_existing_folder(self) -> Path:
        for key in (
            "csv_source",
            "txt_output",
            "txt_input",
            "csv_output",
            "image_archive",
            "extract_output",
            "replacement",
            "rebuilt_tac",
        ):
            value = self.settings.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            candidate = Path(value).expanduser()
            if candidate.is_dir():
                return candidate
            if candidate.parent.is_dir():
                return candidate.parent
        return Path.home()

    def _save_settings(self) -> None:
        variables = {
            "csv_source": self.csv_source_var,
            "txt_output": self.txt_output_var,
            "txt_input": self.txt_input_var,
            "csv_output": self.csv_output_var,
            "image_archive": self.image_archive_var,
            "extract_output": self.extract_output_var,
            "replacement": self.replacement_var,
            "rebuilt_tac": self.rebuilt_tac_var,
        }
        data = {key: variable.get().strip() for key, variable in variables.items()}
        data.update(
            {
                "prefill_translation": bool(self.prefill_var.get()),
                "encoding": self.encoding_var.get(),
                "images_only": bool(self.images_only_var.get()),
                "strict_dimensions": bool(self.strict_dimensions_var.get()),
            }
        )
        try:
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, self.settings_file)
            self.settings = data
        except OSError:
            # A settings failure must never block translation or extraction.
            pass

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.option_add("*Font", ("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 20), foreground="#183153")
        style.configure("Subtitle.TLabel", font=("Segoe UI", 10), foreground="#52606d")
        style.configure("Section.TLabelframe.Label", font=("Segoe UI Semibold", 10))
        style.configure("Accent.TButton", font=("Segoe UI Semibold", 10), padding=(14, 7))
        style.configure("Treeview", rowheight=25)

    def _build_ui(self) -> None:
        header = ttk.Frame(self, padding=(20, 16, 20, 8))
        header.pack(fill="x")
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Dialogues CSV ↔ TXT clair, extraction et reconstruction des archives d'images TanukiSoft.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        # Pack the status and log from the bottom before the expanding notebook,
        # so they remain visible even on a 768 px-high display.
        status = ttk.Frame(self, padding=(18, 0, 18, 12))
        status.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="Prêt.")
        ttk.Label(status, textvariable=self.status_var).pack(side="left")
        self.progress = ttk.Progressbar(status, length=300, mode="determinate")
        self.progress.pack(side="right", padx=(8, 0))
        self.cancel_button = ttk.Button(status, text="Annuler", command=self.cancel_event.set, state="disabled")
        self.cancel_button.pack(side="right")

        log_frame = ttk.Frame(self, padding=(18, 0, 18, 8))
        log_frame.pack(fill="x", side="bottom")
        self.log = tk.Text(
            log_frame,
            height=3,
            wrap="word",
            state="disabled",
            background="#f6f8fa",
            foreground="#28323c",
            relief="solid",
            borderwidth=1,
        )
        self.log.pack(fill="x")

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=18, pady=(4, 8))
        dialogue_tab = ttk.Frame(notebook, padding=12)
        image_tab = ttk.Frame(notebook, padding=12)
        notebook.add(dialogue_tab, text="  Dialogues  ")
        notebook.add(image_tab, text="  Images (.tac)  ")
        self._build_dialogue_tab(dialogue_tab)
        self._build_image_tab(image_tab)

    def _path_row(self, parent, variable: tk.StringVar, browse_command, *, save: bool = False):
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=3)
        entry = ttk.Entry(frame, textvariable=variable)
        entry.pack(side="left", fill="x", expand=True)
        ttk.Button(frame, text="Parcourir…", command=browse_command).pack(side="left", padx=(6, 0))
        return frame

    def _build_dialogue_tab(self, parent) -> None:
        source_box = ttk.LabelFrame(parent, text="1. Choisir les CSV", style="Section.TLabelframe", padding=10)
        source_box.pack(fill="x")
        self.csv_source_var = tk.StringVar(value=self._saved_path("csv_source"))
        row = ttk.Frame(source_box)
        row.pack(fill="x")
        ttk.Entry(row, textvariable=self.csv_source_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Parcourir…", command=lambda: self._choose_dir(self.csv_source_var)).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(row, text="Analyser", command=self.analyse_csv).pack(side="left", padx=(6, 0))

        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill="both", expand=True, pady=(10, 8))
        self.csv_tree = ttk.Treeview(
            tree_frame,
            columns=("role", "rows", "texts", "encoding"),
            show="tree headings",
            selectmode="extended",
            height=6,
        )
        self.csv_tree.heading("#0", text="Fichier")
        self.csv_tree.heading("role", text="Rôle")
        self.csv_tree.heading("rows", text="Lignes CSV")
        self.csv_tree.heading("texts", text="Textes")
        self.csv_tree.heading("encoding", text="Encodage")
        self.csv_tree.column("#0", width=260)
        self.csv_tree.column("role", width=180)
        self.csv_tree.column("rows", width=100, anchor="e")
        self.csv_tree.column("texts", width=100, anchor="e")
        self.csv_tree.column("encoding", width=100, anchor="center")
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.csv_tree.yview)
        self.csv_tree.configure(yscrollcommand=scrollbar.set)
        self.csv_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        select_row = ttk.Frame(parent)
        select_row.pack(fill="x", pady=(0, 8))
        ttk.Button(select_row, text="Scripts du projet", command=self._select_project_scripts).pack(side="left")
        ttk.Button(select_row, text="Tout sélectionner", command=self._select_all_scripts).pack(
            side="left", padx=(6, 0)
        )
        ttk.Label(
            select_row,
            text="Les fichiers hashés sont détectés mais laissés hors sélection par défaut.",
            style="Subtitle.TLabel",
        ).pack(side="right")

        action_area = ttk.Frame(parent)
        action_area.pack(fill="x")
        export_box = ttk.LabelFrame(
            action_area, text="2. Exporter vers des TXT", style="Section.TLabelframe", padding=10
        )
        export_box.pack(side="left", fill="both", expand=True, padx=(0, 5))
        self.txt_output_var = tk.StringVar(value=self._saved_path("txt_output"))
        self._path_row(export_box, self.txt_output_var, lambda: self._choose_dir(self.txt_output_var))
        self.prefill_var = tk.BooleanVar(value=bool(self.settings.get("prefill_translation", True)))
        ttk.Checkbutton(
            export_box,
            text="Préremplir la traduction avec le texte source",
            variable=self.prefill_var,
        ).pack(anchor="w", pady=(5, 7))
        ttk.Button(export_box, text="Exporter les TXT", style="Accent.TButton", command=self.export_txt).pack(
            anchor="e"
        )

        import_box = ttk.LabelFrame(
            action_area, text="3. Réimporter les traductions", style="Section.TLabelframe", padding=10
        )
        import_box.pack(side="left", fill="both", expand=True, padx=(5, 0))
        self.txt_input_var = tk.StringVar(value=self._saved_path("txt_input"))
        self.csv_output_var = tk.StringVar(value=self._saved_path("csv_output"))
        self._path_row(import_box, self.txt_input_var, lambda: self._choose_dir(self.txt_input_var))
        self._path_row(import_box, self.csv_output_var, lambda: self._choose_dir(self.csv_output_var))
        saved_encoding = self.settings.get("encoding", "Compatible jeu — CP932, accents simplifiés")
        self.encoding_var = tk.StringVar(
            value=saved_encoding if isinstance(saved_encoding, str) else "Compatible jeu — CP932, accents simplifiés"
        )
        encoding = ttk.Combobox(
            import_box,
            textvariable=self.encoding_var,
            state="readonly",
            values=(
                "Compatible jeu — CP932, accents simplifiés",
                "CP932 strict — erreur sur les accents",
                "UTF-8 BOM — expérimental",
            ),
        )
        encoding.pack(fill="x", pady=(5, 7))
        ttk.Button(import_box, text="Créer les CSV traduits", style="Accent.TButton", command=self.import_txt).pack(
            anchor="e"
        )

    def _build_image_tab(self, parent) -> None:
        archive_box = ttk.LabelFrame(parent, text="1. Archive d'images", style="Section.TLabelframe", padding=10)
        archive_box.pack(fill="x")
        self.image_archive_var = tk.StringVar(value=self._saved_path("image_archive"))
        row = ttk.Frame(archive_box)
        row.pack(fill="x")
        ttk.Entry(row, textvariable=self.image_archive_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Parcourir…", command=self._choose_tac).pack(side="left", padx=(6, 0))
        ttk.Button(row, text="Analyser", command=self.analyse_tac).pack(side="left", padx=(6, 0))

        summary_box = ttk.LabelFrame(parent, text="Contenu détecté", style="Section.TLabelframe", padding=8)
        summary_box.pack(fill="both", expand=True, pady=10)
        self.image_summary = ttk.Treeview(
            summary_box, columns=("count", "kind"), show="tree headings", height=7
        )
        self.image_summary.heading("#0", text="Dossier / format")
        self.image_summary.heading("count", text="Nombre")
        self.image_summary.heading("kind", text="Catégorie")
        self.image_summary.column("#0", width=360)
        self.image_summary.column("count", width=100, anchor="e")
        self.image_summary.column("kind", width=180)
        self.image_summary.pack(fill="both", expand=True)

        action_area = ttk.Frame(parent)
        action_area.pack(fill="x")
        extract_box = ttk.LabelFrame(
            action_area, text="2. Extraire", style="Section.TLabelframe", padding=10
        )
        extract_box.pack(side="left", fill="both", expand=True, padx=(0, 5))
        self.extract_output_var = tk.StringVar(value=self._saved_path("extract_output"))
        self._path_row(extract_box, self.extract_output_var, lambda: self._choose_dir(self.extract_output_var))
        self.images_only_var = tk.BooleanVar(value=bool(self.settings.get("images_only", True)))
        ttk.Checkbutton(extract_box, text="Images uniquement (PNG/JPG)", variable=self.images_only_var).pack(
            anchor="w", pady=(5, 7)
        )
        ttk.Button(extract_box, text="Extraire", style="Accent.TButton", command=self.extract_images).pack(
            anchor="e"
        )

        rebuild_box = ttk.LabelFrame(
            action_area, text="3. Réinsérer et reconstruire", style="Section.TLabelframe", padding=10
        )
        rebuild_box.pack(side="left", fill="both", expand=True, padx=(5, 0))
        self.replacement_var = tk.StringVar(value=self._saved_path("replacement"))
        self.rebuilt_tac_var = tk.StringVar(value=self._saved_path("rebuilt_tac"))
        self._path_row(rebuild_box, self.replacement_var, lambda: self._choose_dir(self.replacement_var))
        row = ttk.Frame(rebuild_box)
        row.pack(fill="x", pady=3)
        ttk.Entry(row, textvariable=self.rebuilt_tac_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Parcourir…", command=self._choose_output_tac).pack(side="left", padx=(6, 0))
        self.strict_dimensions_var = tk.BooleanVar(value=bool(self.settings.get("strict_dimensions", True)))
        ttk.Checkbutton(
            rebuild_box,
            text="Exiger les mêmes dimensions",
            variable=self.strict_dimensions_var,
        ).pack(anchor="w", pady=(5, 7))
        ttk.Button(
            rebuild_box,
            text="Créer le nouveau TAC",
            style="Accent.TButton",
            command=self.rebuild_tac,
        ).pack(anchor="e")

    def _choose_dir(self, variable: tk.StringVar) -> None:
        chosen = filedialog.askdirectory(initialdir=str(self._initial_directory(variable)))
        if chosen:
            variable.set(chosen)
            self.base_folder = Path(chosen)
            self._save_settings()

    def _initial_directory(self, variable: tk.StringVar) -> Path:
        value = variable.get().strip()
        if value:
            candidate = Path(value).expanduser()
            if candidate.is_dir():
                return candidate
            if candidate.parent.is_dir():
                return candidate.parent
        return self.base_folder if self.base_folder.is_dir() else Path.home()

    def _choose_tac(self) -> None:
        chosen = filedialog.askopenfilename(
            initialdir=str(self._initial_directory(self.image_archive_var)),
            filetypes=(("Archives TAC", "*.tac"), ("Tous les fichiers", "*.*")),
        )
        if chosen:
            self.image_archive_var.set(chosen)
            path = Path(chosen)
            self.extract_output_var.set(str(path.with_name(path.stem + "_extracted")))
            self.replacement_var.set(str(path.with_name(path.stem + "_extracted")))
            self.rebuilt_tac_var.set(str(path.with_name(path.stem + "_fr.tac")))
            self.archive = None
            self.base_folder = path.parent
            self._save_settings()

    def _choose_output_tac(self) -> None:
        current = self.rebuilt_tac_var.get().strip()
        chosen = filedialog.asksaveasfilename(
            initialdir=str(self._initial_directory(self.rebuilt_tac_var)),
            initialfile=Path(current).name if current else "",
            defaultextension=".tac",
            filetypes=(("Archives TAC", "*.tac"),),
        )
        if chosen:
            self.rebuilt_tac_var.set(chosen)
            self.base_folder = Path(chosen).parent
            self._save_settings()

    def _write_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def analyse_csv(self) -> None:
        self._save_settings()
        try:
            self.script_infos = discover_scripts(self.csv_source_var.get())
        except Exception as exc:
            self._show_error(exc)
            return
        self.csv_tree.delete(*self.csv_tree.get_children())
        project_items = []
        for info in self.script_infos:
            role = "Script du projet" if info.project_script else "Doublon / hash"
            item = self.csv_tree.insert(
                "", "end", iid=info.filename, text=info.filename, values=(role, info.rows, info.text_rows, info.encoding)
            )
            if info.project_script:
                project_items.append(item)
        self.csv_tree.selection_set(project_items)
        project_count = sum(info.project_script for info in self.script_infos)
        texts = sum(info.text_rows for info in self.script_infos if info.project_script)
        self._write_log(f"CSV : {project_count} scripts du projet, {texts:,} cellules de texte à traiter.")
        self.status_var.set("Analyse CSV terminée.")

    def _select_project_scripts(self) -> None:
        self.csv_tree.selection_set([info.filename for info in self.script_infos if info.project_script])

    def _select_all_scripts(self) -> None:
        self.csv_tree.selection_set(self.csv_tree.get_children())

    def export_txt(self) -> None:
        self._save_settings()
        selected = list(self.csv_tree.selection())
        source = self.csv_source_var.get()
        output = self.txt_output_var.get()
        prefill = self.prefill_var.get()
        self._start_job(
            "Export des dialogues…",
            lambda: export_dialogues(
                source,
                output,
                selected,
                prefill_translation=prefill,
            ),
            lambda report: self._write_log(
                f"Export terminé : {report.lines:,} textes dans {report.files} fichiers TXT — {report.output_dir}"
            ),
        )

    def import_txt(self) -> None:
        self._save_settings()
        modes = {
            "Compatible jeu — CP932, accents simplifiés": "cp932_safe",
            "CP932 strict — erreur sur les accents": "cp932_strict",
            "UTF-8 BOM — expérimental": "utf8_bom",
        }
        source = self.csv_source_var.get()
        translations = self.txt_input_var.get()
        output = self.csv_output_var.get()
        mode = modes[self.encoding_var.get()]
        self._start_job(
            "Réimportation des traductions…",
            lambda: import_dialogues(
                source,
                translations,
                output,
                encoding_mode=mode,
            ),
            lambda report: self._write_log(
                f"CSV créés : {report.translated:,} traductions appliquées dans {report.files_written} fichiers; "
                f"{report.simplified_characters:,} caractères adaptés au CP932 — {report.output_dir}"
            ),
        )

    def analyse_tac(self) -> None:
        self._save_settings()
        archive_path = self.image_archive_var.get()

        def work():
            archive = TacArchive(archive_path)
            return archive, archive.summary()

        def done(result):
            self.archive, summary = result
            self.image_summary.delete(*self.image_summary.get_children())
            folder_root = self.image_summary.insert("", "end", text="Dossiers", values=(summary.entries, "Total"), open=True)
            for name, count in summary.folders.items():
                self.image_summary.insert(folder_root, "end", text=name, values=(count, "Dossier"))
            format_root = self.image_summary.insert("", "end", text="Formats", values=(summary.images, "Images"), open=True)
            for extension, count in summary.extensions.items():
                self.image_summary.insert(format_root, "end", text=extension, values=(count, "Format"))
            self._write_log(
                f"{Path(archive_path).name} : {summary.entries} entrées, "
                f"{summary.named_entries} noms résolus, {summary.images} images."
            )

        self._start_job("Analyse de l'archive…", work, done)

    def _progress_callback(self, current: int, total: int, name: str) -> None:
        self.events.put(("progress", current, total, name))

    def extract_images(self) -> None:
        self._save_settings()
        destination = Path(self.extract_output_var.get())
        if destination.exists() and any(destination.iterdir()):
            if not messagebox.askyesno(
                APP_TITLE, "Le dossier d'extraction contient déjà des fichiers. Continuer et remplacer les doublons ?"
            ):
                return

        archive_path = self.image_archive_var.get()
        images_only = self.images_only_var.get()

        def work():
            archive = TacArchive(archive_path)
            report = archive.extract(
                destination,
                images_only=images_only,
                progress=self._progress_callback,
                cancel=self.cancel_event,
            )
            return archive, report

        def done(result):
            self.archive, report = result
            self._write_log(
                f"Extraction terminée : {report.extracted} fichiers, {report.bytes_written / 1024 / 1024:.1f} Mio — "
                f"{report.output_dir}"
            )

        self._start_job(
            "Extraction des images…",
            work,
            done,
        )

    def rebuild_tac(self) -> None:
        self._save_settings()
        output = Path(self.rebuilt_tac_var.get())
        if output.exists() and not messagebox.askyesno(APP_TITLE, f"Remplacer le fichier de sortie existant ?\n{output}"):
            return

        archive_path = self.image_archive_var.get()
        replacements = self.replacement_var.get()
        strict_dimensions = self.strict_dimensions_var.get()

        def work():
            archive = TacArchive(archive_path)
            report = archive.rebuild(
                replacements,
                output,
                strict_dimensions=strict_dimensions,
                progress=self._progress_callback,
                cancel=self.cancel_event,
            )
            return archive, report

        def done(result):
            self.archive, report = result
            self._write_log(
                f"Archive vérifiée : {report.replaced} fichier(s) remplacé(s), "
                f"{report.output_size / 1024 / 1024:.1f} Mio — {report.output_path}"
            )

        self._start_job(
            "Reconstruction du TAC…",
            work,
            done,
        )

    def _start_job(self, label: str, work, done=None) -> None:
        if self.job_running:
            messagebox.showinfo(APP_TITLE, "Une opération est déjà en cours.")
            return
        self.job_running = True
        self.cancel_event.clear()
        self.status_var.set(label)
        self.progress.configure(value=0, maximum=100)
        self.cancel_button.configure(state="normal")

        def runner():
            try:
                result = work()
            except Exception as exc:
                self.events.put(("error", exc, traceback.format_exc()))
            else:
                self.events.put(("done", result, done))

        threading.Thread(target=runner, daemon=True).start()

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "progress":
                    _, current, total, name = event
                    self.progress.configure(maximum=max(total, 1), value=current)
                    self.status_var.set(f"{current}/{total} — {name}")
                elif kind == "error":
                    _, error, details = event
                    self.job_running = False
                    self.cancel_button.configure(state="disabled")
                    self.status_var.set("Opération interrompue.")
                    self._write_log(details)
                    self._show_error(error)
                elif kind == "done":
                    _, result, callback = event
                    self.job_running = False
                    self.cancel_button.configure(state="disabled")
                    self.progress.configure(value=self.progress.cget("maximum"))
                    self.status_var.set("Terminé.")
                    if callback:
                        callback(result)
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _show_error(self, error: Exception) -> None:
        if isinstance(error, OperationCancelled):
            messagebox.showinfo(APP_TITLE, str(error))
        elif isinstance(error, (CsvToolError, TacError)):
            messagebox.showerror(APP_TITLE, str(error))
        else:
            messagebox.showerror(APP_TITLE, f"Erreur inattendue : {error}")

    def _on_close(self) -> None:
        if self.job_running:
            if not messagebox.askyesno(APP_TITLE, "Une opération est en cours. L'annuler et fermer ?"):
                return
            self.cancel_event.set()
        self._save_settings()
        self.destroy()


def run() -> None:
    app = TanukiToolsApp()
    app.mainloop()

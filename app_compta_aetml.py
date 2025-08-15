import customtkinter as ctk
from tkinter import ttk, messagebox, filedialog
import sqlite3
from datetime import datetime
from fpdf import FPDF
from fpdf.enums import XPos, YPos
import os
import shutil
import webbrowser
import sys
import requests
from packaging.version import parse as parse_version

# --- CONFIGURATION ---
APP_VERSION = "1.0.6"  # Version actuelle de l'application
DB_FILE = "aetml_compta.db"
APP_TITLE = "AETML - Gestion Comptable"
ATTACHMENT_DIR = "attachments"
REPORTS_DIR = "reports"
SAVE_DIR = "save"

CATEGORIES = {
    "recette": ["Recettes babyfoot", "Dons", "Sponsoring", "Cotisations", "Autre Recette"],
    "depense": ["Frais de production", "Frais de communication", "Frais de représentation", "Charges financières", "Taxe bancaire", "Prix et sponsoring", "Achats matériel", "Autre Dépense"]
}
DENOMINATIONS = [100, 50, 20, 10, 5, 2, 1, 0.5, 0.2, 0.1, 0.05]

# --- GESTION DE LA BASE DE DONNÉES (SQLite) ---
def db_connect():
    """Initialise la connexion à la base de données et crée les tables si elles n'existent pas."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS accounting_years (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, start_date TEXT, end_date TEXT)")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY, date TEXT, journal TEXT, libelle TEXT, category TEXT,
            type TEXT, amount REAL, year_id INTEGER, attachment_path TEXT,
            FOREIGN KEY (year_id) REFERENCES accounting_years (id))
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            id INTEGER PRIMARY KEY, year_id INTEGER, category TEXT, amount REAL,
            UNIQUE(year_id, category), FOREIGN KEY (year_id) REFERENCES accounting_years (id))
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cash_details (
            id INTEGER PRIMARY KEY, entry_id INTEGER, denomination REAL, count INTEGER,
            FOREIGN KEY (entry_id) REFERENCES entries(id) ON DELETE CASCADE)
    """)
    cursor.execute("PRAGMA table_info(entries)")
    columns = [info[1] for info in cursor.fetchall()]
    if 'year_id' not in columns:
        cursor.execute("ALTER TABLE entries ADD COLUMN year_id INTEGER REFERENCES accounting_years(id)")
    if 'attachment_path' not in columns:
        cursor.execute("ALTER TABLE entries ADD COLUMN attachment_path TEXT")
    conn.commit()
    return conn

# --- GÉNÉRATION PDF ---
class PDF(FPDF):
    def header(self):
        self.set_font('Helvetica', 'B', 12)
        self.cell(0, 10, APP_TITLE, 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.cell(0, 10, f'Page {self.page_no()}', 0, align='C')

def _draw_journal_report(pdf, data, year_name, journal_type):
    title = "Journal de Caisse" if journal_type == 'caisse' else "Journal de Poste"
    pdf.set_font('Helvetica', 'B', 14)
    pdf.cell(0, 10, f'{title} - Exercice {year_name}', 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
    pdf.ln(5)
    pdf.set_fill_color(220, 220, 220)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.cell(25, 8, 'Date', 1, align='C', fill=True)
    pdf.cell(45, 8, 'Catégorie', 1, align='C', fill=True)
    pdf.cell(60, 8, 'Libellé', 1, align='C', fill=True)
    pdf.cell(25, 8, 'Montant', 1, align='C', fill=True)
    pdf.cell(25, 8, 'Solde', 1, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C', fill=True)
    pdf.set_font('Helvetica', '', 9)
    solde = 0
    journal_entries = sorted([e for e in data if e['journal'] == journal_type], key=lambda x: x['date'])
    for entry in journal_entries:
        solde += entry['amount']
        safe_category = entry['category'].encode('latin-1', 'replace').decode('latin-1')
        safe_libelle = entry['libelle'].encode('latin-1', 'replace').decode('latin-1')
        pdf.cell(25, 7, datetime.strptime(entry['date'], '%Y-%m-%d').strftime('%d/%m/%Y'), 1)
        pdf.cell(45, 7, safe_category, 1)
        pdf.cell(60, 7, safe_libelle, 1)
        pdf.cell(25, 7, f"{entry['amount']:.2f}", 1, align='R')
        pdf.cell(25, 7, f"{solde:.2f}", 1, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
    return f"{title.replace(' ', '_')}.pdf"

def _draw_resultat_report(pdf, data, year_name):
    pdf.set_font('Helvetica', 'B', 14)
    pdf.cell(0, 10, f'Compte de Résultat - Exercice {year_name}', 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
    pdf.ln(5)
    total_recettes = 0
    pdf.set_font('Helvetica', 'B', 12)
    pdf.cell(0, 10, "Produits (Recettes)", 'B', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font('Helvetica', '', 10)
    for cat in CATEGORIES['recette']:
        cat_total = sum(e['amount'] for e in data if e['category'] == cat)
        if cat_total > 0:
            total_recettes += cat_total
            pdf.cell(130, 7, cat.encode('latin-1', 'replace').decode('latin-1'))
            pdf.cell(40, 7, f"{cat_total:.2f}", 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
    pdf.set_font('Helvetica', 'B', 10)
    pdf.cell(130, 8, "Total des Produits", 'T', align='R')
    pdf.cell(40, 8, f"{total_recettes:.2f}", 'T', new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
    pdf.ln(10)
    total_depenses = 0
    pdf.set_font('Helvetica', 'B', 12)
    pdf.cell(0, 10, "Charges (Dépenses)", 'B', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font('Helvetica', '', 10)
    for cat in CATEGORIES['depense']:
        cat_total = sum(e['amount'] for e in data if e['category'] == cat)
        if cat_total < 0:
            total_depenses += abs(cat_total)
            pdf.cell(130, 7, cat.encode('latin-1', 'replace').decode('latin-1'))
            pdf.cell(40, 7, f"{abs(cat_total):.2f}", 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
    pdf.set_font('Helvetica', 'B', 10)
    pdf.cell(130, 8, "Total des Charges", 'T', align='R')
    pdf.cell(40, 8, f"{total_depenses:.2f}", 'T', new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
    pdf.ln(10)
    benefice = total_recettes - total_depenses
    resultat_text = "Bénéfice de l'exercice" if benefice >= 0 else "Perte de l'exercice"
    pdf.set_font('Helvetica', 'B', 12)
    pdf.cell(130, 8, resultat_text, align='R')
    pdf.cell(40, 8, f"{benefice:.2f}", 1, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
    return "Compte_de_Resultat.pdf"

def _draw_budget_report(pdf, budget_data, actual_data, year_name):
    pdf.set_font('Helvetica', 'B', 14)
    pdf.cell(0, 10, f'Rapport de Budget - Exercice {year_name}', 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
    pdf.ln(5)
    pdf.set_font('Helvetica', 'B', 10)
    pdf.set_fill_color(220, 220, 220)
    pdf.cell(80, 8, 'Catégorie', 1, align='C', fill=True)
    pdf.cell(30, 8, 'Budgeté', 1, align='C', fill=True)
    pdf.cell(30, 8, 'Réel', 1, align='C', fill=True)
    pdf.cell(30, 8, 'Différence', 1, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C', fill=True)

    def draw_category_table(title, categories):
        pdf.set_font('Helvetica', 'B', 11)
        pdf.cell(0, 10, title, 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        total_budget, total_actual = 0.0, 0.0
        for cat in categories:
            budget = budget_data.get(cat, 0.0)
            actual = abs(actual_data.get(cat, 0.0))
            diff = budget - actual
            total_budget += budget
            total_actual += actual
            pdf.set_font('Helvetica', '', 9)
            pdf.cell(80, 7, cat.encode('latin-1', 'replace').decode('latin-1'), 1)
            pdf.cell(30, 7, f"{budget:.2f}", 1, align='R')
            pdf.cell(30, 7, f"{actual:.2f}", 1, align='R')
            pdf.cell(30, 7, f"{diff:.2f}", 1, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
        pdf.set_font('Helvetica', 'B', 9)
        pdf.cell(80, 7, f"Total {title}", 1, align='R')
        pdf.cell(30, 7, f"{total_budget:.2f}", 1, align='R')
        pdf.cell(30, 7, f"{total_actual:.2f}", 1, align='R')
        pdf.cell(30, 7, f"{total_budget - total_actual:.2f}", 1, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
        return total_budget, total_actual

    total_budget_rec, total_actual_rec = draw_category_table("Recettes", CATEGORIES['recette'])
    pdf.ln(5)
    total_budget_dep, total_actual_dep = draw_category_table("Dépenses", CATEGORIES['depense'])
    pdf.ln(10)
    pdf.set_font('Helvetica', 'B', 12)
    pdf.cell(80, 8, "Résultat Budgeté", align='R')
    pdf.cell(40, 8, f"{total_budget_rec - total_budget_dep:.2f}", 1, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
    pdf.cell(80, 8, "Résultat Réel", align='R')
    pdf.cell(40, 8, f"{total_actual_rec - total_actual_dep:.2f}", 1, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
    return "Rapport_Budget.pdf"

def generate_pdf(report_type, year_name, **kwargs):
    safe_year_name = year_name.replace('/', '-').replace('\\', '-')
    year_report_dir = os.path.join(REPORTS_DIR, safe_year_name)
    os.makedirs(year_report_dir, exist_ok=True)
    pdf = PDF()
    pdf.add_page()
    filename = ""
    report_drawers = {
        'caisse': lambda: _draw_journal_report(pdf, kwargs.get('data'), year_name, 'caisse'),
        'poste': lambda: _draw_journal_report(pdf, kwargs.get('data'), year_name, 'poste'),
        'resultat': lambda: _draw_resultat_report(pdf, kwargs.get('data'), year_name),
        'budget': lambda: _draw_budget_report(pdf, kwargs.get('budget_data'), kwargs.get('actual_data'), year_name),
    }
    if report_type in report_drawers:
        filename = report_drawers[report_type]()
    else:
        messagebox.showwarning("Non implémenté", f"Le rapport de type '{report_type}' n'est pas configuré.")
        return
    if filename:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        final_filename = f"{os.path.splitext(filename)[0]}_{timestamp}.pdf"
        filepath = os.path.join(year_report_dir, final_filename)
        try:
            pdf.output(filepath)
            messagebox.showinfo("Succès", f"Le rapport a été généré ici :\n{os.path.abspath(filepath)}")
        except Exception as e:
            messagebox.showerror("Erreur de sauvegarde PDF", f"Impossible de sauvegarder le fichier:\n{e}")

# --- APPLICATION PRINCIPALE ---
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # Exécute le nettoyage de l'ancienne version au tout début
        self.cleanup_old_version()
        
        self.title(f"{APP_TITLE} - v{APP_VERSION}")
        self.geometry("1200x750")

        os.makedirs(ATTACHMENT_DIR, exist_ok=True)
        os.makedirs(REPORTS_DIR, exist_ok=True)
        os.makedirs(SAVE_DIR, exist_ok=True)

        self.conn = db_connect()
        self.current_year_id = None
        self.accounting_years = {}

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self.sidebar_frame = ctk.CTkFrame(self, width=180, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(9, weight=1)
        self.logo_label = ctk.CTkLabel(self.sidebar_frame, text="AETML Compta", font=ctk.CTkFont(size=20, weight="bold"))
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 10))

        self.main_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.main_frame.grid(row=1, column=1, sticky="nsew", padx=20, pady=(0, 20))
        self.main_frame.grid_rowconfigure(0, weight=1)
        self.main_frame.grid_columnconfigure(0, weight=1)

        self.dashboard_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.journal_poste_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.journal_caisse_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.reports_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.years_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.budget_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")

        self.create_sidebar_buttons()
        self.setup_topbar()
        self.setup_dashboard()
        self.setup_journal_view(self.journal_poste_frame, "poste")
        self.setup_journal_view(self.journal_caisse_frame, "caisse")
        self.setup_reports_view()
        self.setup_years_view()
        self.setup_budget_view()

        self.update_year_selector()
        self.select_frame_by_name("dashboard")
        
        # Lance la vérification des mises à jour 2 secondes après le démarrage
        self.after(2000, self.check_for_updates)

    def cleanup_old_version(self):
        """Supprime l'ancienne version du script (_old.py) si elle existe."""
        # Tente de trouver le nom du script actuel pour construire l'ancien nom
        try:
            current_script_name = os.path.basename(sys.argv[0])
            base_name, ext = os.path.splitext(current_script_name)
            old_script_path = f"{base_name}_old{ext}"
            
            if os.path.exists(old_script_path):
                os.remove(old_script_path)
                print(f"Ancienne version '{old_script_path}' supprimée avec succès.")
        except Exception as e:
            print(f"Impossible de supprimer l'ancienne version : {e}")

    def check_for_updates(self):
        """Vérifie sur GitHub si une nouvelle version est disponible."""
        version_url = "https://raw.githubusercontent.com/AE2TML/app-compta-aetml/main/version.txt"
        
        try:
            # Augmentation du timeout à 15 secondes
            response = requests.get(version_url, timeout=15)
            if response.status_code == 200:
                remote_version_str = response.text.strip()
                local_version = parse_version(APP_VERSION)
                remote_version = parse_version(remote_version_str)

                if remote_version > local_version:
                    if messagebox.askyesno("Mise à jour disponible", 
                                           f"Une nouvelle version ({remote_version_str}) est disponible.\n"
                                           f"Votre version actuelle est la {APP_VERSION}.\n\n"
                                           "Voulez-vous la télécharger et l'installer maintenant ?"):
                        self.apply_update()
        except requests.RequestException as e:
            print(f"Erreur lors de la vérification des mises à jour : {e}")

    def apply_update(self):
        """Télécharge la nouvelle version et lance le script de mise à jour."""
        release_url = "https://github.com/AE2TML/app-compta-aetml/releases/latest/download/app_compta_aetml.py"
        
        try:
            response = requests.get(release_url, stream=True)
            response.raise_for_status()
            
            # Utilise des noms de fichiers clairs pour le processus
            current_script_name = os.path.basename(sys.argv[0])
            base_name, ext = os.path.splitext(current_script_name)
            new_script_path = f"{base_name}_new{ext}"
            old_script_path = f"{base_name}_old{ext}"
            updater_script_path = "updater.bat"

            with open(new_script_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            # Crée le script batch pour remplacer l'ancien fichier de manière robuste
            with open(updater_script_path, "w") as f:
                f.write(f"@echo off\n")
                f.write(f"echo Mise a jour de l'application...\n")
                f.write(f"timeout /t 3 /nobreak > nul\n") # Donne 3s à l'app pour se fermer
                f.write(f"rename \"{current_script_name}\" \"{old_script_path}\"\n") # Renomme l'ancien script
                f.write(f"rename \"{new_script_path}\" \"{current_script_name}\"\n") # Renomme le nouveau script
                f.write(f"echo Lancement de la nouvelle version...\n")
                f.write(f"start python \"{current_script_name}\"\n") # Démarre la nouvelle version
                f.write(f"del \"%~f0\"\n") # Le script se supprime lui-même

            os.startfile(updater_script_path)
            self.destroy()

        except requests.RequestException as e:
            messagebox.showerror("Erreur de téléchargement", f"Impossible de télécharger la mise à jour : {e}")
        except Exception as e:
            messagebox.showerror("Erreur", f"Une erreur inattendue est survenue : {e}")

    def create_sidebar_buttons(self):
        self.dashboard_button = ctk.CTkButton(self.sidebar_frame, text="Tableau de Bord", command=self.dashboard_frame_event)
        self.dashboard_button.grid(row=1, column=0, padx=20, pady=10)
        self.journal_poste_button = ctk.CTkButton(self.sidebar_frame, text="Journal de Poste", command=self.journal_poste_frame_event)
        self.journal_poste_button.grid(row=2, column=0, padx=20, pady=10)
        self.journal_caisse_button = ctk.CTkButton(self.sidebar_frame, text="Journal de Caisse", command=self.journal_caisse_frame_event)
        self.journal_caisse_button.grid(row=3, column=0, padx=20, pady=10)
        self.reports_button = ctk.CTkButton(self.sidebar_frame, text="Rapports", command=self.reports_frame_event)
        self.reports_button.grid(row=4, column=0, padx=20, pady=10)
        self.budget_button = ctk.CTkButton(self.sidebar_frame, text="Budget", command=self.budget_frame_event)
        self.budget_button.grid(row=5, column=0, padx=20, pady=10)
        self.years_button = ctk.CTkButton(self.sidebar_frame, text="Exercices", command=self.years_frame_event)
        self.years_button.grid(row=6, column=0, padx=20, pady=10)
        self.save_button = ctk.CTkButton(self.sidebar_frame, text="Sauvegarder", command=self.backup_database)
        self.save_button.grid(row=7, column=0, padx=20, pady=10)
        self.load_button = ctk.CTkButton(self.sidebar_frame, text="Charger une sauvegarde", command=self.restore_database)
        self.load_button.grid(row=8, column=0, padx=20, pady=10)

    def setup_topbar(self):
        self.topbar_frame = ctk.CTkFrame(self, height=50, corner_radius=0, fg_color="transparent")
        self.topbar_frame.grid(row=0, column=1, sticky="ew", padx=20, pady=10)
        ctk.CTkLabel(self.topbar_frame, text="Exercice Actif:").pack(side="left")
        self.year_selector_var = ctk.StringVar(value="Aucun exercice sélectionné")
        self.year_selector = ctk.CTkOptionMenu(self.topbar_frame, variable=self.year_selector_var, command=self.on_year_selected)
        self.year_selector.pack(side="left", padx=10)
        
    def setup_dashboard(self):
        self.dashboard_frame.grid_columnconfigure((0, 1), weight=1)
        self.solde_poste_card = ctk.CTkFrame(self.dashboard_frame)
        self.solde_poste_card.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        ctk.CTkLabel(self.solde_poste_card, text="Solde Compte Postal (Exercice)", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(10,0))
        self.solde_poste_label = ctk.CTkLabel(self.solde_poste_card, text="0.00 CHF", font=ctk.CTkFont(size=24))
        self.solde_poste_label.pack(pady=10, padx=20)
        self.solde_caisse_card = ctk.CTkFrame(self.dashboard_frame)
        self.solde_caisse_card.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")
        ctk.CTkLabel(self.solde_caisse_card, text="Solde Caisse (Exercice)", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(10,0))
        self.solde_caisse_label = ctk.CTkLabel(self.solde_caisse_card, text="0.00 CHF", font=ctk.CTkFont(size=24))
        self.solde_caisse_label.pack(pady=10, padx=20)
        self.resultat_card = ctk.CTkFrame(self.dashboard_frame)
        self.resultat_card.grid(row=1, column=0, columnspan=2, padx=10, pady=10, sticky="nsew")
        self.resultat_card.grid_columnconfigure((0,1), weight=1)
        ctk.CTkLabel(self.resultat_card, text="Résultat de l'exercice", font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, columnspan=2, pady=(10,5))
        ctk.CTkLabel(self.resultat_card, text="Total Recettes").grid(row=1, column=0)
        self.total_recettes_label = ctk.CTkLabel(self.resultat_card, text="0.00 CHF", font=ctk.CTkFont(size=18), text_color="green")
        self.total_recettes_label.grid(row=2, column=0, pady=(0,10))
        ctk.CTkLabel(self.resultat_card, text="Total Dépenses").grid(row=1, column=1)
        self.total_depenses_label = ctk.CTkLabel(self.resultat_card, text="0.00 CHF", font=ctk.CTkFont(size=18), text_color="red")
        self.total_depenses_label.grid(row=2, column=1, pady=(0,10))
        ctk.CTkLabel(self.resultat_card, text="Bénéfice / Perte", font=ctk.CTkFont(weight="bold")).grid(row=3, column=0, columnspan=2, pady=(10,0))
        self.benefice_label = ctk.CTkLabel(self.resultat_card, text="0.00 CHF", font=ctk.CTkFont(size=22, weight="bold"))
        self.benefice_label.grid(row=4, column=0, columnspan=2, pady=(0,10))

    def setup_journal_view(self, frame, journal_type):
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        title = "Journal de Poste" if journal_type == "poste" else "Journal de Caisse"
        ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(size=22, weight="bold")).grid(row=0, column=0, sticky="w", pady=(0,10))

        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview", background="#2b2b2b", foreground="white", fieldbackground="#2b2b2b", borderwidth=0)
        style.map('Treeview', background=[('selected', '#22559b')])
        style.configure("Treeview.Heading", background="#565b5e", foreground="white", font=('Calibri', 10, 'bold'))

        tree = ttk.Treeview(frame, columns=("ID", "Date", "Libellé", "Catégorie", "Débit", "Crédit", "Solde", "Pièce"), show="headings")
        headings = {"ID": 40, "Date": 100, "Libellé": 250, "Catégorie": 150, "Débit": 100, "Crédit": 100, "Solde": 100, "Pièce": 50}
        for col, width in headings.items():
            tree.heading(col, text=col)
            tree.column(col, width=width, anchor="center")

        tree.grid(row=1, column=0, sticky="nsew")
        setattr(self, f"{journal_type}_tree", tree)
        tree.bind("<<TreeviewSelect>>", lambda event, jt=journal_type: self.on_journal_select(event, jt))

        totals_frame = ctk.CTkFrame(frame, fg_color="transparent")
        totals_frame.grid(row=2, column=0, sticky="ew", pady=(5,0))
        totals_frame.grid_columnconfigure((0,1,2,3,4,5,6,7), weight=1)

        total_credit_label = ctk.CTkLabel(totals_frame, text="Total Crédit: 0.00", font=ctk.CTkFont(weight="bold"))
        total_credit_label.grid(row=0, column=5, sticky="e")
        setattr(self, f"{journal_type}_total_credit_label", total_credit_label)

        total_debit_label = ctk.CTkLabel(totals_frame, text="Total Débit: 0.00", font=ctk.CTkFont(weight="bold"))
        total_debit_label.grid(row=0, column=4, sticky="e")
        setattr(self, f"{journal_type}_total_debit_label", total_debit_label)

        solde_final_label = ctk.CTkLabel(totals_frame, text="Solde Final: 0.00", font=ctk.CTkFont(weight="bold"))
        solde_final_label.grid(row=0, column=6, sticky="e")
        setattr(self, f"{journal_type}_solde_final_label", solde_final_label)

        button_frame = ctk.CTkFrame(frame, fg_color="transparent")
        button_frame.grid(row=3, column=0, pady=10, sticky="e")

        view_attachment_button = ctk.CTkButton(button_frame, text="Voir Pièce/Détail", state="disabled", command=lambda: self.view_attachment(journal_type))
        view_attachment_button.pack(side="left", padx=5)
        setattr(self, f"{journal_type}_view_attachment_button", view_attachment_button)

        ctk.CTkButton(button_frame, text="Ajouter Écriture", command=lambda: self.open_entry_window(journal_type, edit_mode=False)).pack(side="left", padx=5)
        edit_button = ctk.CTkButton(button_frame, text="Modifier Écriture", state="disabled", command=lambda: self.open_entry_window(journal_type, edit_mode=True))
        edit_button.pack(side="left", padx=5)
        setattr(self, f"{journal_type}_edit_button", edit_button)
        delete_button = ctk.CTkButton(button_frame, text="Supprimer Écriture", state="disabled", command=lambda: self.delete_entry(journal_type))
        delete_button.pack(side="left", padx=5)
        setattr(self, f"{journal_type}_delete_button", delete_button)

    def setup_reports_view(self):
        self.reports_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self.reports_frame, text="Génération de Rapports", font=ctk.CTkFont(size=22, weight="bold")).pack(pady=(0,20))
        ctk.CTkButton(self.reports_frame, text="Générer Journal de Caisse (PDF)", command=lambda: self.generate_report('caisse')).pack(pady=10, padx=20)
        ctk.CTkButton(self.reports_frame, text="Générer Journal de Poste (PDF)", command=lambda: self.generate_report('poste')).pack(pady=10, padx=20)
        ctk.CTkButton(self.reports_frame, text="Générer Compte de Résultat (PDF)", command=lambda: self.generate_report('resultat')).pack(pady=10, padx=20)
        ctk.CTkButton(self.reports_frame, text="Générer Budget (PDF)", command=lambda: self.generate_report('budget')).pack(pady=10, padx=20)

    def setup_years_view(self):
        self.years_frame.grid_columnconfigure(0, weight=1)
        self.years_frame.grid_rowconfigure(1, weight=1)
        form_frame = ctk.CTkFrame(self.years_frame)
        form_frame.grid(row=0, column=0, sticky="ew", pady=10)
        ctk.CTkLabel(form_frame, text="Nom (ex: 2024-2025)").pack(side="left", padx=(10,2))
        self.year_name_entry = ctk.CTkEntry(form_frame, placeholder_text="Nom de l'exercice")
        self.year_name_entry.pack(side="left", padx=2, expand=True)
        ctk.CTkLabel(form_frame, text="Début (YYYY-MM-DD)").pack(side="left", padx=(10,2))
        self.start_date_entry = ctk.CTkEntry(form_frame, placeholder_text="Date de début")
        self.start_date_entry.pack(side="left", padx=2, expand=True)
        ctk.CTkLabel(form_frame, text="Fin (YYYY-MM-DD)").pack(side="left", padx=(10,2))
        self.end_date_entry = ctk.CTkEntry(form_frame, placeholder_text="Date de fin")
        self.end_date_entry.pack(side="left", padx=2, expand=True)
        ctk.CTkButton(form_frame, text="Ajouter Exercice", command=self.add_year).pack(side="left", padx=10)

        self.years_tree = ttk.Treeview(self.years_frame, columns=("ID", "Nom", "Début", "Fin"), show="headings")
        self.years_tree.heading("ID", text="ID"); self.years_tree.column("ID", width=50)
        self.years_tree.heading("Nom", text="Nom"); self.years_tree.column("Nom", width=200)
        self.years_tree.heading("Début", text="Date de début"); self.years_tree.column("Début", width=150)
        self.years_tree.heading("Fin", text="Date de fin"); self.years_tree.column("Fin", width=150)
        self.years_tree.grid(row=1, column=0, sticky="nsew")
        self.refresh_years_view()

    def setup_budget_view(self):
        self.budget_frame.grid_rowconfigure(0, weight=1)
        self.budget_frame.grid_columnconfigure(0, weight=1)

        self.tabview = ctk.CTkTabview(self.budget_frame)
        self.tabview.grid(row=0, column=0, sticky="nsew")
        self.tabview.add("Créer / Modifier le Budget")
        self.tabview.add("Suivi du Budget")

        self.budget_edit_frame = ctk.CTkScrollableFrame(self.tabview.tab("Créer / Modifier le Budget"))
        self.budget_edit_frame.pack(expand=True, fill="both")

        self.budget_entries = {}
        row = 0
        ctk.CTkLabel(self.budget_edit_frame, text="Revenus", font=ctk.CTkFont(size=16, weight="bold")).grid(row=row, column=0, columnspan=2, pady=10, sticky="w")
        row += 1
        for cat in CATEGORIES["recette"]:
            label = ctk.CTkLabel(self.budget_edit_frame, text=cat)
            label.grid(row=row, column=0, padx=10, pady=5, sticky="w")
            entry = ctk.CTkEntry(self.budget_edit_frame, placeholder_text="0.00")
            entry.grid(row=row, column=1, padx=10, pady=5, sticky="ew")
            self.budget_entries[cat] = entry
            row += 1

        ctk.CTkLabel(self.budget_edit_frame, text="Dépenses", font=ctk.CTkFont(size=16, weight="bold")).grid(row=row, column=0, columnspan=2, pady=10, sticky="w")
        row += 1
        for cat in CATEGORIES["depense"]:
            label = ctk.CTkLabel(self.budget_edit_frame, text=cat)
            label.grid(row=row, column=0, padx=10, pady=5, sticky="w")
            entry = ctk.CTkEntry(self.budget_edit_frame, placeholder_text="0.00")
            entry.grid(row=row, column=1, padx=10, pady=5, sticky="ew")
            self.budget_entries[cat] = entry
            row += 1

        save_button = ctk.CTkButton(self.budget_edit_frame, text="Sauvegarder le Budget", command=self.save_budget)
        save_button.grid(row=row, column=0, columnspan=2, pady=20)

        self.budget_view_frame = ctk.CTkFrame(self.tabview.tab("Suivi du Budget"))
        self.budget_view_frame.pack(expand=True, fill="both")

    def update_year_selector(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, name, start_date, end_date FROM accounting_years ORDER BY start_date DESC")
        years = cursor.fetchall()
        self.accounting_years.clear()
        year_names = []
        for year in years:
            self.accounting_years[year['name']] = {'id': year['id'], 'start': year['start_date'], 'end': year['end_date']}
            year_names.append(year['name'])
        if not year_names:
            year_names = ["Créez un exercice d'abord"]
        self.year_selector.configure(values=year_names)
        if year_names and year_names[0] != "Créez un exercice d'abord":
            self.year_selector_var.set(year_names[0])
            self.on_year_selected(year_names[0])
        else:
            self.year_selector_var.set(year_names[0])
            self.on_year_selected(None)

    def on_year_selected(self, selected_year_name):
        if selected_year_name and selected_year_name in self.accounting_years:
            self.current_year_id = self.accounting_years[selected_year_name]['id']
        else:
            self.current_year_id = None
        self.refresh_all_views()

    def refresh_all_views(self):
        self.update_dashboard()
        self.refresh_journal_view("poste")
        self.refresh_journal_view("caisse")
        self.update_budget_view()
        self.load_budget_for_editing()

    def select_frame_by_name(self, name):
        buttons = {"dashboard": self.dashboard_button, "poste": self.journal_poste_button,
                   "caisse": self.journal_caisse_button, "reports": self.reports_button,
                   "years": self.years_button, "budget": self.budget_button}
        
        bold_font = ctk.CTkFont(weight="bold")
        normal_font = ctk.CTkFont(weight="normal")

        for btn_name, button in buttons.items():
            button.configure(font=bold_font if name == btn_name else normal_font)

        frames = {"dashboard": self.dashboard_frame, "poste": self.journal_poste_frame,
                  "caisse": self.journal_caisse_frame, "reports": self.reports_frame,
                  "years": self.years_frame, "budget": self.budget_frame}
        for frame_name, frame in frames.items():
            if name == frame_name:
                frame.grid(row=0, column=0, sticky="nsew")
            else:
                frame.grid_forget()
        if name == "budget":
            self.load_budget_for_editing()
            self.update_budget_view()

    def dashboard_frame_event(self): self.select_frame_by_name("dashboard")
    def journal_poste_frame_event(self): self.select_frame_by_name("poste")
    def journal_caisse_frame_event(self): self.select_frame_by_name("caisse")
    def reports_frame_event(self): self.select_frame_by_name("reports")
    def years_frame_event(self): self.select_frame_by_name("years")
    def budget_frame_event(self): self.select_frame_by_name("budget")

    def get_entries_for_selected_year(self):
        if not self.current_year_id: return []
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM entries WHERE year_id = ? ORDER BY date DESC", (self.current_year_id,))
        return cursor.fetchall()

    def get_entry_by_id(self, entry_id):
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM entries WHERE id = ?", (entry_id,))
        return cursor.fetchone()

    def update_dashboard(self):
        entries = self.get_entries_for_selected_year()
        solde_poste = sum(e['amount'] for e in entries if e['journal'] == 'poste')
        solde_caisse = sum(e['amount'] for e in entries if e['journal'] == 'caisse')
        total_recettes = sum(e['amount'] for e in entries if e['type'] == 'recette')
        total_depenses = sum(abs(e['amount']) for e in entries if e['type'] == 'depense')
        benefice = total_recettes - total_depenses
        self.solde_poste_label.configure(text=f"{solde_poste:.2f} CHF")
        self.solde_caisse_label.configure(text=f"{solde_caisse:.2f} CHF")
        self.total_recettes_label.configure(text=f"{total_recettes:.2f} CHF")
        self.total_depenses_label.configure(text=f"{total_depenses:.2f} CHF")
        self.benefice_label.configure(text=f"{benefice:.2f} CHF")

    def refresh_journal_view(self, journal_type):
        tree = getattr(self, f"{journal_type}_tree")
        for item in tree.get_children():
            tree.delete(item)

        total_debit_label = getattr(self, f"{journal_type}_total_debit_label")
        total_credit_label = getattr(self, f"{journal_type}_total_credit_label")
        solde_final_label = getattr(self, f"{journal_type}_solde_final_label")

        if not self.current_year_id:
            total_debit_label.configure(text="Total Débit: 0.00")
            total_credit_label.configure(text="Total Crédit: 0.00")
            solde_final_label.configure(text="Solde Final: 0.00")
            return

        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM entries WHERE journal = ? AND year_id = ? ORDER BY date ASC, id ASC", (journal_type, self.current_year_id))
        entries = cursor.fetchall()

        solde = 0
        total_debit = 0
        total_credit = 0
        for entry in entries:
            solde += entry['amount']
            debit = f"{abs(entry['amount']):.2f}" if entry['amount'] < 0 else ""
            credit = f"{entry['amount']:.2f}" if entry['amount'] >= 0 else ""
            if entry['amount'] < 0: total_debit += abs(entry['amount'])
            else: total_credit += entry['amount']

            attachment_indicator = ""
            if entry['attachment_path']:
                attachment_indicator = "📄"
            elif journal_type == 'caisse':
                cursor.execute("SELECT 1 FROM cash_details WHERE entry_id = ?", (entry['id'],))
                if cursor.fetchone():
                    attachment_indicator = "💰"

            tree.insert("", "end", values=(entry['id'], datetime.strptime(entry['date'], '%Y-%m-%d').strftime('%d/%m/%Y'), entry['libelle'], entry['category'], debit, credit, f"{solde:.2f}", attachment_indicator))

        total_debit_label.configure(text=f"Total Débit: {total_debit:.2f}")
        total_credit_label.configure(text=f"Total Crédit: {total_credit:.2f}")
        solde_final_label.configure(text=f"Solde Final: {solde:.2f}")

        view_button = getattr(self, f"{journal_type}_view_attachment_button")
        edit_button = getattr(self, f"{journal_type}_edit_button")
        delete_button = getattr(self, f"{journal_type}_delete_button")
        view_button.configure(state="disabled")
        edit_button.configure(state="disabled")
        delete_button.configure(state="disabled")

    def on_journal_select(self, event, journal_type):
        tree = getattr(self, f"{journal_type}_tree")
        view_button = getattr(self, f"{journal_type}_view_attachment_button")
        edit_button = getattr(self, f"{journal_type}_edit_button")
        delete_button = getattr(self, f"{journal_type}_delete_button")

        selected_items = tree.selection()
        if not selected_items:
            view_button.configure(state="disabled")
            edit_button.configure(state="disabled")
            delete_button.configure(state="disabled")
            return

        edit_button.configure(state="normal")
        delete_button.configure(state="normal")
        selected_item = selected_items[0]
        attachment_indicator = tree.item(selected_item, "values")[7]

        if attachment_indicator:
            view_button.configure(state="normal")
        else:
            view_button.configure(state="disabled")

    def open_entry_window(self, journal_type, edit_mode=False):
        if not self.current_year_id:
            messagebox.showerror("Erreur", "Veuillez d'abord sélectionner ou créer un exercice comptable.")
            return

        entry_data = None
        entry_id = None
        if edit_mode:
            tree = getattr(self, f"{journal_type}_tree")
            if not tree.focus():
                messagebox.showwarning("Attention", "Veuillez sélectionner une écriture à modifier.")
                return
            entry_id = tree.item(tree.focus())['values'][0]
            entry_data = self.get_entry_by_id(entry_id)
            if not entry_data:
                messagebox.showerror("Erreur", "L'écriture sélectionnée n'a pas pu être trouvée.")
                return

        win_title = "Modifier une Écriture" if edit_mode else "Nouvelle Écriture"
        win = ctk.CTkToplevel(self)
        win.title(win_title); win.geometry("450x500"); win.transient(self)

        attachment_path = ctk.StringVar()
        if edit_mode and entry_data['attachment_path']:
            attachment_path.set(os.path.join(ATTACHMENT_DIR, entry_data['attachment_path']))

        cash_details_data = {}

        def select_file():
            filepath = filedialog.askopenfilename(title="Sélectionner un justificatif PDF", filetypes=[("PDF files", "*.pdf")])
            if filepath:
                attachment_path.set(filepath)
                attachment_label.configure(text=os.path.basename(filepath))

        def open_cash_details():
            details_win = ctk.CTkToplevel(win)
            details_win.title("Détail de la monnaie"); details_win.transient(win)

            entries = {}
            for i, denom in enumerate(DENOMINATIONS):
                ctk.CTkLabel(details_win, text=f"{denom:.2f} CHF").grid(row=i, column=0, padx=10, pady=5)
                entry = ctk.CTkEntry(details_win)
                entry.grid(row=i, column=1, padx=10, pady=5)
                entries[denom] = entry

            def calculate_total():
                total = 0
                cash_details_data.clear()
                for denom, entry in entries.items():
                    try:
                        count = int(entry.get() or 0)
                        if count > 0:
                            cash_details_data[denom] = count
                            total += denom * count
                    except ValueError:
                        pass
                amount_entry.delete(0, 'end')
                amount_entry.insert(0, f"{total:.2f}")
                details_win.destroy()

            ctk.CTkButton(details_win, text="Valider", command=calculate_total).grid(row=len(DENOMINATIONS), column=0, columnspan=2, pady=10)

        ctk.CTkLabel(win, text="Date:").grid(row=0, column=0, padx=10, pady=5, sticky="w")
        date_entry = ctk.CTkEntry(win, placeholder_text="YYYY-MM-DD")
        date_entry.grid(row=0, column=1, columnspan=2, padx=10, pady=5, sticky="ew")
        date_entry.insert(0, entry_data['date'] if edit_mode else datetime.now().strftime('%Y-%m-%d'))

        ctk.CTkLabel(win, text="Libellé:").grid(row=1, column=0, padx=10, pady=5, sticky="w")
        libelle_entry = ctk.CTkEntry(win)
        libelle_entry.grid(row=1, column=1, columnspan=2, padx=10, pady=5, sticky="ew")
        if edit_mode: libelle_entry.insert(0, entry_data['libelle'])

        ctk.CTkLabel(win, text="Type:").grid(row=2, column=0, padx=10, pady=5, sticky="w")
        type_var = ctk.StringVar(value=entry_data['type'] if edit_mode else "depense")
        def update_cat_menu(selected_type):
            cat_menu.configure(values=CATEGORIES[selected_type])
            if not edit_mode or (edit_mode and selected_type != entry_data['type']):
                cat_var.set(CATEGORIES[selected_type][0])
        type_menu = ctk.CTkOptionMenu(win, variable=type_var, values=["depense", "recette"], command=update_cat_menu)
        type_menu.grid(row=2, column=1, columnspan=2, padx=10, pady=5, sticky="ew")

        ctk.CTkLabel(win, text="Catégorie:").grid(row=3, column=0, padx=10, pady=5, sticky="w")
        cat_var = ctk.StringVar(value=entry_data['category'] if edit_mode else CATEGORIES[type_var.get()][0])
        cat_menu = ctk.CTkOptionMenu(win, variable=cat_var, values=CATEGORIES[type_var.get()])
        cat_menu.grid(row=3, column=1, columnspan=2, padx=10, pady=5, sticky="ew")

        ctk.CTkLabel(win, text="Montant (CHF):").grid(row=4, column=0, padx=10, pady=5, sticky="w")
        amount_entry = ctk.CTkEntry(win)
        amount_entry.grid(row=4, column=1, columnspan=2, padx=10, pady=5, sticky="ew")
        if edit_mode: amount_entry.insert(0, f"{abs(entry_data['amount']):.2f}")

        if journal_type == 'caisse':
            ctk.CTkButton(win, text="Détailler la monnaie...", command=open_cash_details).grid(row=5, column=0, columnspan=3, pady=5)

        ctk.CTkLabel(win, text="Justificatif:").grid(row=6, column=0, padx=10, pady=5, sticky="w")
        ctk.CTkButton(win, text="Joindre un PDF...", command=select_file).grid(row=6, column=1, padx=10, pady=5, sticky="ew")
        attachment_label_text = os.path.basename(attachment_path.get()) if attachment_path.get() else "Aucun fichier"
        attachment_label = ctk.CTkLabel(win, text=attachment_label_text, text_color="gray", anchor="w")
        attachment_label.grid(row=7, column=1, columnspan=2, padx=10, pady=(0,10), sticky="ew")

        if edit_mode:
            save_button = ctk.CTkButton(win, text="Sauvegarder", command=lambda: self.update_entry(win, entry_id, journal_type, date_entry.get(), libelle_entry.get(), type_var.get(), cat_var.get(), amount_entry.get(), new_attachment_path=attachment_path.get(), cash_details=cash_details_data, old_db_attachment_path=entry_data['attachment_path']))
        else:
            save_button = ctk.CTkButton(win, text="Sauvegarder", command=lambda: self.save_entry(win, journal_type, date_entry.get(), libelle_entry.get(), type_var.get(), cat_var.get(), amount_entry.get(), attachment_path.get(), cash_details_data))
        save_button.grid(row=8, column=0, columnspan=3, padx=10, pady=20)

    def save_entry(self, win, journal_type, date_str, libelle, type_op, category, amount_str, source_attachment_path, cash_details):
        year_name = self.year_selector_var.get()
        year_info = self.accounting_years.get(year_name)
        try:
            entry_date = datetime.strptime(date_str, '%Y-%m-%d')
            start_date = datetime.strptime(year_info['start'], '%Y-%m-%d')
            end_date = datetime.strptime(year_info['end'], '%Y-%m-%d')
            if not (start_date <= entry_date <= end_date):
                messagebox.showerror("Erreur de date", f"La date doit être dans l'exercice actif.", parent=win)
                return
        except (ValueError, TypeError):
            messagebox.showerror("Erreur de date", "Format de date invalide (YYYY-MM-DD) ou exercice non sélectionné.", parent=win)
            return

        try:
            amount = float(amount_str)
            if type_op == 'depense': amount = -abs(amount)
        except ValueError:
            messagebox.showerror("Erreur", "Le montant doit être un nombre.", parent=win)
            return

        if not libelle:
            messagebox.showerror("Erreur", "Le libellé est requis.", parent=win)
            return

        db_attachment_path = None
        if source_attachment_path:
            try:
                year_folder = os.path.join(ATTACHMENT_DIR, str(self.current_year_id))
                os.makedirs(year_folder, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                filename = f"{timestamp}_{os.path.basename(source_attachment_path)}"
                dest_path = os.path.join(year_folder, filename)
                shutil.copy(source_attachment_path, dest_path)
                db_attachment_path = os.path.join(str(self.current_year_id), filename)
            except Exception as e:
                messagebox.showerror("Erreur Fichier", f"Impossible de copier le justificatif : {e}", parent=win)
                return

        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO entries (date, journal, libelle, category, type, amount, year_id, attachment_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (date_str, journal_type, libelle, category, type_op, amount, self.current_year_id, db_attachment_path))

        if journal_type == 'caisse' and cash_details:
            entry_id = cursor.lastrowid
            cursor.execute("DELETE FROM cash_details WHERE entry_id = ?", (entry_id,))
            for denom, count in cash_details.items():
                cursor.execute("INSERT INTO cash_details (entry_id, denomination, count) VALUES (?, ?, ?)", (entry_id, denom, count))

        self.conn.commit()
        self.refresh_all_views()
        win.destroy()

    def update_entry(self, win, entry_id, journal_type, date_str, libelle, type_op, category, amount_str, new_attachment_path, cash_details, old_db_attachment_path):
        try:
            amount = float(amount_str)
            if type_op == 'depense': amount = -abs(amount)
        except ValueError:
            messagebox.showerror("Erreur", "Le montant doit être un nombre.", parent=win)
            return

        db_attachment_path = old_db_attachment_path
        if new_attachment_path and os.path.join(ATTACHMENT_DIR, str(old_db_attachment_path or '')) != new_attachment_path:
            if old_db_attachment_path and os.path.exists(os.path.join(ATTACHMENT_DIR, old_db_attachment_path)):
                os.remove(os.path.join(ATTACHMENT_DIR, old_db_attachment_path))
            try:
                year_folder = os.path.join(ATTACHMENT_DIR, str(self.current_year_id))
                os.makedirs(year_folder, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                filename = f"{timestamp}_{os.path.basename(new_attachment_path)}"
                dest_path = os.path.join(year_folder, filename)
                shutil.copy(new_attachment_path, dest_path)
                db_attachment_path = os.path.join(str(self.current_year_id), filename)
            except Exception as e:
                messagebox.showerror("Erreur Fichier", f"Impossible de copier le nouveau justificatif : {e}", parent=win)
                return

        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE entries SET date = ?, libelle = ?, category = ?, type = ?, amount = ?, attachment_path = ?
            WHERE id = ?
        """, (date_str, libelle, category, type_op, amount, db_attachment_path, entry_id))

        if journal_type == 'caisse':
            cursor.execute("DELETE FROM cash_details WHERE entry_id = ?", (entry_id,))
            if cash_details:
                for denom, count in cash_details.items():
                    cursor.execute("INSERT INTO cash_details (entry_id, denomination, count) VALUES (?, ?, ?)", (entry_id, denom, count))

        self.conn.commit()
        self.refresh_all_views()
        win.destroy()

    def delete_entry(self, journal_type):
        tree = getattr(self, f"{journal_type}_tree")
        if not tree.focus():
            messagebox.showwarning("Attention", "Veuillez sélectionner une écriture à supprimer.")
            return

        selected_item = tree.item(tree.focus())
        entry_id = selected_item['values'][0]
        entry_data = self.get_entry_by_id(entry_id)
        attachment_path_str = entry_data['attachment_path'] if entry_data else None

        if messagebox.askyesno("Confirmation", f"Êtes-vous sûr de vouloir supprimer l'écriture ID {entry_id} ?"):
            if attachment_path_str:
                full_path = os.path.join(ATTACHMENT_DIR, attachment_path_str)
                if os.path.exists(full_path):
                    try:
                        os.remove(full_path)
                    except OSError as e:
                        messagebox.showerror("Erreur", f"Impossible de supprimer la pièce jointe: {e}")

            self.conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
            self.conn.commit()
            self.refresh_all_views()

    def view_attachment(self, journal_type):
        tree = getattr(self, f"{journal_type}_tree")
        if not tree.focus(): return

        entry_id = tree.item(tree.focus())['values'][0]
        cursor = self.conn.cursor()
        cursor.execute("SELECT attachment_path FROM entries WHERE id = ?", (entry_id,))
        result = cursor.fetchone()

        if result and result[0]:
            file_path = os.path.join(ATTACHMENT_DIR, result[0])
            if os.path.exists(file_path):
                try:
                    webbrowser.open(f'file://{os.path.realpath(file_path)}')
                except Exception as e:
                    messagebox.showerror("Erreur", f"Impossible d'ouvrir le fichier : {e}")
            else:
                messagebox.showerror("Erreur", "Fichier non trouvé.")
        elif journal_type == 'caisse':
            cursor.execute("SELECT denomination, count FROM cash_details WHERE entry_id = ? ORDER BY denomination DESC", (entry_id,))
            details = cursor.fetchall()
            if details:
                details_win = ctk.CTkToplevel(self)
                details_win.title(f"Détail Caisse - Écriture {entry_id}")
                details_win.transient(self)
                total = 0
                for i, (denom, count) in enumerate(details):
                    amount = denom * count
                    total += amount
                    ctk.CTkLabel(details_win, text=f"{count} x {denom:.2f} CHF = {amount:.2f} CHF").pack(anchor="w", padx=10, pady=2)
                ctk.CTkLabel(details_win, text=f"Total: {total:.2f} CHF", font=ctk.CTkFont(weight="bold")).pack(pady=10)
            else:
                messagebox.showinfo("Information", "Aucun détail pour cette écriture.")

    def add_year(self):
        name = self.year_name_entry.get()
        start = self.start_date_entry.get()
        end = self.end_date_entry.get()
        if not all([name, start, end]):
            messagebox.showerror("Erreur", "Tous les champs sont requis.")
            return
        try:
            datetime.strptime(start, '%Y-%m-%d'); datetime.strptime(end, '%Y-%m-%d')
        except ValueError:
            messagebox.showerror("Erreur", "Format de date invalide. Utilisez YYYY-MM-DD.")
            return
        try:
            cursor = self.conn.cursor()
            cursor.execute("INSERT INTO accounting_years (name, start_date, end_date) VALUES (?, ?, ?)", (name, start, end))
            self.conn.commit()
            self.refresh_years_view()
            self.update_year_selector()
            self.year_name_entry.delete(0, 'end'); self.start_date_entry.delete(0, 'end'); self.end_date_entry.delete(0, 'end')
        except sqlite3.IntegrityError:
            messagebox.showerror("Erreur", "Un exercice avec ce nom existe déjà.")

    def refresh_years_view(self):
        for item in self.years_tree.get_children(): self.years_tree.delete(item)
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, name, start_date, end_date FROM accounting_years ORDER BY start_date DESC")
        for row in cursor.fetchall():
            self.years_tree.insert("", "end", values=(row['id'], row['name'], row['start_date'], row['end_date']))

    def save_budget(self):
        if not self.current_year_id:
            messagebox.showerror("Erreur", "Veuillez sélectionner un exercice.")
            return
        cursor = self.conn.cursor()
        for category, entry_widget in self.budget_entries.items():
            amount_str = entry_widget.get()
            try:
                amount = float(amount_str) if amount_str else 0.0
                cursor.execute("""
                    INSERT INTO budgets (year_id, category, amount) VALUES (?, ?, ?)
                    ON CONFLICT(year_id, category) DO UPDATE SET amount = excluded.amount
                """, (self.current_year_id, category, amount))
            except ValueError:
                messagebox.showerror("Erreur", f"Montant invalide pour la catégorie '{category}'.")
                return
        self.conn.commit()
        messagebox.showinfo("Succès", "Budget sauvegardé.")
        self.update_budget_view()

    def load_budget_for_editing(self):
        for entry in self.budget_entries.values():
            entry.delete(0, 'end')
        if not self.current_year_id: return
        cursor = self.conn.cursor()
        cursor.execute("SELECT category, amount FROM budgets WHERE year_id = ?", (self.current_year_id,))
        for row in cursor.fetchall():
            category, amount = row['category'], row['amount']
            if category in self.budget_entries:
                self.budget_entries[category].insert(0, f"{amount:.2f}")

    def update_budget_view(self):
        for widget in self.budget_view_frame.winfo_children():
            widget.destroy()
        if not self.current_year_id:
            ctk.CTkLabel(self.budget_view_frame, text="Veuillez sélectionner un exercice pour voir le budget.").pack()
            return

        main_budget_frame = ctk.CTkScrollableFrame(self.budget_view_frame)
        main_budget_frame.pack(expand=True, fill="both")
        main_budget_frame.grid_columnconfigure((0, 1), weight=1)

        revenu_frame = ctk.CTkFrame(main_budget_frame)
        revenu_frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        charges_frame = ctk.CTkFrame(main_budget_frame)
        charges_frame.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")
        result_frame = ctk.CTkFrame(main_budget_frame)
        result_frame.grid(row=1, column=0, columnspan=2, padx=10, pady=10, sticky="nsew")

        cursor = self.conn.cursor()
        cursor.execute("SELECT category, amount FROM budgets WHERE year_id = ?", (self.current_year_id,))
        budget_data = {row['category']: row['amount'] for row in cursor.fetchall()}
        cursor.execute("SELECT category, SUM(amount) FROM entries WHERE year_id = ? GROUP BY category", (self.current_year_id,))
        actual_data = {row['category']: row[1] for row in cursor.fetchall()}

        header_font = ctk.CTkFont(size=12, weight="bold")

        ctk.CTkLabel(revenu_frame, text="Revenu", font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=5)
        ctk.CTkLabel(revenu_frame, text="Catégorie", font=header_font).grid(row=1, column=0, sticky="w")
        ctk.CTkLabel(revenu_frame, text="Budgeté", font=header_font).grid(row=1, column=1, sticky="e")
        ctk.CTkLabel(revenu_frame, text="Réel", font=header_font).grid(row=1, column=2, sticky="e")

        total_budget_recettes, total_actual_recettes = 0.0, 0.0
        row = 2
        for cat in CATEGORIES["recette"]:
            budget_amount = budget_data.get(cat, 0.0)
            actual_amount = actual_data.get(cat, 0.0)
            total_budget_recettes += budget_amount
            total_actual_recettes += actual_amount
            ctk.CTkLabel(revenu_frame, text=cat).grid(row=row, column=0, sticky="w")
            ctk.CTkLabel(revenu_frame, text=f"{budget_amount:.2f}").grid(row=row, column=1, sticky="e")
            ctk.CTkLabel(revenu_frame, text=f"{actual_amount:.2f}", text_color="green").grid(row=row, column=2, sticky="e")
            row += 1

        ctk.CTkLabel(revenu_frame, text="Total Revenus", font=header_font).grid(row=row, column=0, sticky="w", pady=(5,0))
        ctk.CTkLabel(revenu_frame, text=f"{total_budget_recettes:.2f}", font=header_font).grid(row=row, column=1, sticky="e")
        ctk.CTkLabel(revenu_frame, text=f"{total_actual_recettes:.2f}", font=header_font, text_color="green").grid(row=row, column=2, sticky="e")

        ctk.CTkLabel(charges_frame, text="Charges", font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=5)
        ctk.CTkLabel(charges_frame, text="Catégorie", font=header_font).grid(row=1, column=0, sticky="w")
        ctk.CTkLabel(charges_frame, text="Budgeté", font=header_font).grid(row=1, column=1, sticky="e")
        ctk.CTkLabel(charges_frame, text="Réel", font=header_font).grid(row=1, column=2, sticky="e")

        total_budget_depenses, total_actual_depenses = 0.0, 0.0
        row = 2
        for cat in CATEGORIES["depense"]:
            budget_amount = budget_data.get(cat, 0.0)
            actual_amount = abs(actual_data.get(cat, 0.0))
            total_budget_depenses += budget_amount
            total_actual_depenses += actual_amount
            ctk.CTkLabel(charges_frame, text=cat).grid(row=row, column=0, sticky="w")
            ctk.CTkLabel(charges_frame, text=f"{budget_amount:.2f}").grid(row=row, column=1, sticky="e")
            ctk.CTkLabel(charges_frame, text=f"{actual_amount:.2f}", text_color="red").grid(row=row, column=2, sticky="e")
            row += 1

        ctk.CTkLabel(charges_frame, text="Total Charges", font=header_font).grid(row=row, column=0, sticky="w", pady=(5,0))
        ctk.CTkLabel(charges_frame, text=f"{total_budget_depenses:.2f}", font=header_font).grid(row=row, column=1, sticky="e")
        ctk.CTkLabel(charges_frame, text=f"{total_actual_depenses:.2f}", font=header_font, text_color="red").grid(row=row, column=2, sticky="e")

        benefice_budget = total_budget_recettes - total_budget_depenses
        benefice_actual = total_actual_recettes - total_actual_depenses
        ctk.CTkLabel(result_frame, text="Bénéfice / Perte", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(result_frame, text=f"Budgeté: {benefice_budget:.2f} CHF", font=header_font).grid(row=0, column=1, sticky="e", padx=20)
        ctk.CTkLabel(result_frame, text=f"Réel: {benefice_actual:.2f} CHF", font=header_font).grid(row=0, column=2, sticky="e", padx=20)

    def generate_report(self, report_type):
        if not self.current_year_id:
            messagebox.showerror("Erreur", "Veuillez sélectionner un exercice.")
            return
        
        year_name = self.year_selector_var.get()
        
        report_kwargs = {}
        if report_type in ['caisse', 'poste', 'resultat', 'exploitation']:
            report_kwargs['data'] = self.get_entries_for_selected_year()
        elif report_type == 'budget':
            cursor = self.conn.cursor()
            cursor.execute("SELECT category, amount FROM budgets WHERE year_id = ?", (self.current_year_id,))
            report_kwargs['budget_data'] = {row['category']: row['amount'] for row in cursor.fetchall()}
            cursor.execute("SELECT category, SUM(amount) FROM entries WHERE year_id = ? GROUP BY category", (self.current_year_id,))
            report_kwargs['actual_data'] = {row['category']: row[1] for row in cursor.fetchall()}

        generate_pdf(report_type=report_type, year_name=year_name, **report_kwargs)

    def backup_database(self):
        try:
            self.conn.close()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_filename = f"backup_{timestamp}.db"
            backup_filepath = os.path.join(SAVE_DIR, backup_filename)
            shutil.copyfile(DB_FILE, backup_filepath)
            self.conn = db_connect()
            messagebox.showinfo("Succès", f"Sauvegarde créée avec succès:\n{backup_filepath}")
        except Exception as e:
            messagebox.showerror("Erreur de sauvegarde", f"Une erreur est survenue: {e}")
            self.conn = db_connect()

    def restore_database(self):
        if not messagebox.askyesno("Confirmation", "Êtes-vous sûr de vouloir charger une sauvegarde ?\nToutes les données non sauvegardées seront écrasées."):
            return
        filepath = filedialog.askopenfilename(
            title="Sélectionner un fichier de sauvegarde",
            initialdir=SAVE_DIR,
            filetypes=[("Fichiers de base de données", "*.db")]
        )
        if not filepath:
            return
        try:
            self.conn.close()
            shutil.copyfile(filepath, DB_FILE)
            self.conn = db_connect()
            self.update_year_selector()
            self.on_year_selected(self.year_selector_var.get())
            self.select_frame_by_name("dashboard")
            messagebox.showinfo("Succès", "La sauvegarde a été chargée avec succès.")
        except Exception as e:
            messagebox.showerror("Erreur de restauration", f"Une erreur est survenue: {e}")
            self.conn = db_connect()

if __name__ == "__main__":
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")
    app = App()
    app.mainloop()

# hello feur

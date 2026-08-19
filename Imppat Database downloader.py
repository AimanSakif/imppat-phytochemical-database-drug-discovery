#!/usr/bin/env python3
"""
IMPPAT -> PubChem 3D Conformer SDF Downloader (GUI)
===================================================

What it does
------------
1. You type a plant name (e.g. "Oryza sativa") in the GUI and press Download.
2. It fills IMPPAT's basic-search form, submits it, and reads EVERY phytochemical
   row for that plant (all pages / the full result set = each IMPHY id).
3. For each phytochemical it opens the IMPPAT detail page and reads the PubChem
   "CID:xxxxx" link.
4. It downloads that compound's **3D Conformer SDF** straight from PubChem
   (identical to Download -> 3D Conformer -> SDF -> Save on the PubChem page).
5. All .sdf files go into a new folder named after the plant, created in the
   current working directory. Live progress is shown in the GUI log.
6. Type a new plant + press Download again -> log clears, a new folder is made,
   and it starts fresh.

Requirements
------------
    pip install selenium requests beautifulsoup4 pandas openpyxl
Plus Google Chrome (or Chromium). Selenium 4.6+ auto-downloads the matching
driver, so you do NOT need chromedriver installed manually.
"""

import os
import re
import sys
import time
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

# --------------------------------------------------------------------------- #
# Dependency checking – show a GUI error if any required package is missing
# --------------------------------------------------------------------------- #
def check_imports():
    """Check that all required third‑party packages are available."""
    missing = []
    try:
        import requests
    except ImportError:
        missing.append("requests")
    try:
        import pandas as pd
    except ImportError:
        missing.append("pandas")
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        missing.append("beautifulsoup4")  # note: package name is 'beautifulsoup4'
    # openpyxl is optional (only for Excel export), we'll handle it later

    if missing:
        root = tk.Tk()
        root.withdraw()
        # Build a clear error message with the correct package names and commands
        pkg_list = ", ".join(missing)
        # special note for beautifulsoup4 to avoid typo "soap"
        note = ""
        if "beautifulsoup4" in missing:
            note = "\n\nNOTE: The package is 'beautifulsoup4' (spelled 'soup', not 'soap')."
        msg = (
            f"The following required packages are not installed:\n\n"
            f"{pkg_list}\n\n"
            f"Please install them with:\n\n"
            f"pip install {pkg_list}\n\n"
            f"(If you use pip3, run: pip3 install {pkg_list})"
            f"{note}"
        )
        messagebox.showerror("Missing Dependencies", msg)
        root.destroy()
        sys.exit(1)

# Check imports before building the GUI
check_imports()

# Now it's safe to import everything
import requests
import pandas as pd
from bs4 import BeautifulSoup

# Selenium is imported lazily inside get_driver() – we'll check it there.

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
SEARCH_URL = "https://cb.imsc.res.in/imppat/basicsearch/phytochemical"
DETAIL_URL = "https://cb.imsc.res.in/imppat/phytochemical-detailedpage/{imphy}"
IMPPAT_3D_SDF = "https://cb.imsc.res.in/imppat/images/3D/SDF/{imphy}_3D.sdf"
PUBCHEM_3D_SDF = ("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/"
                  "cid/{cid}/SDF?record_type=3d")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36")
}

DETAIL_RE = re.compile(r"phytochemical-detailedpage/(IMPHY\d+)", re.I)
CID_RE = re.compile(r"pubchem\.ncbi\.nlm\.nih\.gov/compound/(\d+)", re.I)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def sanitize(name: str) -> str:
    """Make a string safe to use as a file/folder name."""
    name = name.strip()
    name = re.sub(r'[<>:"/\\|?*]', "_", name)   # illegal on Windows
    name = re.sub(r"\s+", " ", name)
    return name.strip(" .") or "unnamed"


def looks_like_sdf(text: str) -> bool:
    """A valid SDF/MOL block has a counts line (V2000/V3000) and ends with $$$$."""
    return ("V2000" in text or "V3000" in text) and "$$$$" in text


# --------------------------------------------------------------------------- #
# Selenium: submit the search form and collect all IMPHY ids
# --------------------------------------------------------------------------- #
def get_driver(headless: bool):
    """Create a Chrome driver (Selenium Manager auto-handles the driver binary)."""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except ImportError:
        raise ImportError("Selenium is not installed. Please run: pip install selenium")

    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1400,900")
    opts.add_argument(f"user-agent={HEADERS['User-Agent']}")
    opts.add_experimental_option("excludeSwitches", ["enable-logging"])
    return webdriver.Chrome(options=opts)


def search_plant_and_get_ids(driver, plant, log, stop_event):
    """Fill the search form, submit, and return an ordered list of unique IMPHY ids."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException

    log("Opening IMPPAT search page ...")
    driver.get(SEARCH_URL)

    wait = WebDriverWait(driver, 30)
    box = wait.until(EC.presence_of_element_located((By.ID, "edit-combine")))
    box.clear()
    box.send_keys(plant)
    log(f"Submitting search for: {plant}")

    submit = driver.find_element(By.ID, "edit-submit-basic-search-phytochemical")
    driver.execute_script("arguments[0].click();", submit)

    # Wait for the AJAX results to be injected into #show-ajax-replay.
    def results_state(d):
        try:
            html = d.find_element(
                By.ID, "show-ajax-replay").get_attribute("innerHTML") or ""
        except Exception:
            return False
        if "phytochemical-detailedpage" in html:
            return "found"
        low = html.lower()
        if html.strip() and any(k in low for k in
                                ("no result", "not found", "no phytochemical",
                                 "no match", "invalid")):
            return "empty"
        return False

    log("Waiting for results ...")
    try:
        state = WebDriverWait(driver, 60).until(results_state)
    except TimeoutException:
        snippet = ""
        try:
            snippet = (driver.find_element(By.ID, "show-ajax-replay")
                       .get_attribute("innerHTML") or "")[:400]
        except Exception:
            pass
        log("[WARN] Timed out waiting for results. Response so far:")
        log(snippet or "(empty)")
        return []

    if state == "empty":
        return []

    if stop_event.is_set():
        return []

    # The results are shown in a paginated table. Force it to reveal ALL rows so
    # every IMPHY id ends up in the DOM, regardless of the table library used.
    driver.execute_script("""
        try {
            if (window.jQuery) {
                var $ = window.jQuery;
                $('#show-ajax-replay table').each(function () {
                    if ($.fn.DataTable && $.fn.DataTable.isDataTable(this)) {
                        $(this).DataTable().page.len(-1).draw();
                    }
                });
            }
        } catch (e) {}
    """)
    # Give the table a moment to redraw
    time.sleep(1.5)

    html = driver.find_element(By.ID, "show-ajax-replay").get_attribute("innerHTML") or ""
    ids, seen = [], set()
    for m in DETAIL_RE.finditer(html):
        imphy = m.group(1).upper()
        if imphy not in seen:
            seen.add(imphy)
            ids.append(imphy)

    # If a "... of N entries" count is present, sanity-check we got them all.
    mcount = re.search(r"of\s+([\d,]+)\s+entries", html)
    if mcount:
        total = int(mcount.group(1).replace(",", ""))
        if total > len(ids):
            log(f"[WARN] Table reports {total} entries but only {len(ids)} ids "
                f"were read from the DOM. Some rows may be paginated out.")
    return ids


# --------------------------------------------------------------------------- #
# requests: detail page -> CID -> download SDF
# --------------------------------------------------------------------------- #
def get_detail_info(session, imphy):
    """Return (phytochemical_name, cid_or_None) from an IMPPAT detail page."""
    url = DETAIL_URL.format(imphy=imphy)
    r = session.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    html = r.text

    m = CID_RE.search(html)
    cid = m.group(1) if m else None

    name = imphy
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")
    hm = re.search(r"IMPPAT Phytochemical information:\s*(.+)", text)
    if hm:
        name = hm.group(1).strip()
    else:
        nm = re.search(r"Phytochemical name:\s*\n?\s*(.+)", text)
        if nm:
            name = nm.group(1).strip()
    name = name.splitlines()[0].strip() if name else imphy
    return name, cid


def download_pubchem_3d(session, cid):
    """Return SDF text for the 3D conformer, or None if PubChem has no 3D record."""
    url = PUBCHEM_3D_SDF.format(cid=cid)
    for attempt in range(3):
        try:
            r = session.get(url, headers=HEADERS, timeout=60)
            if r.status_code == 200 and looks_like_sdf(r.text):
                return r.text
            if r.status_code == 503:          # PubChem throttling -> back off
                time.sleep(2 * (attempt + 1))
                continue
        except requests.RequestException:
            time.sleep(1)
            continue
    return None


def download_imppat_3d(session, imphy):
    """Fallback: IMPPAT's own precomputed 3D SDF (available for every IMPHY id)."""
    url = IMPPAT_3D_SDF.format(imphy=imphy)
    try:
        r = session.get(url, headers=HEADERS, timeout=60)
        if r.status_code == 200 and looks_like_sdf(r.text):
            return r.text
    except requests.RequestException:
        pass
    return None


# --------------------------------------------------------------------------- #
# Worker: the whole pipeline for one plant
# --------------------------------------------------------------------------- #
def run_pipeline(plant, headless, use_fallback, log, stop_event):
    """
    Run the IMPPAT -> PubChem 3D SDF pipeline and create a Pandas
    CSV/Excel summary in the plant output folder.
    """
    plant = plant.strip()
    folder = os.path.join(os.getcwd(), sanitize(plant))
    os.makedirs(folder, exist_ok=True)
    log(f"Output folder: {folder}")

    session = requests.Session()
    records = []

    driver = None
    try:
        try:
            driver = get_driver(headless)
        except Exception as e:
            log(f"[ERROR] Could not start Chrome/Selenium: {e}")
            log("Make sure Google Chrome is installed and you have run:")
            log("  pip install selenium")
            return

        ids = search_plant_and_get_ids(driver, plant, log, stop_event)
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass

    if stop_event.is_set():
        log("Stopped.")
        return

    if not ids:
        log("No phytochemicals found for that plant name. "
            "Check the spelling (e.g. 'Oryza sativa').")
        return

    log(f"\nFound {len(ids)} phytochemicals. Starting downloads ...\n")

    ok = skipped = failed = 0

    for i, imphy in enumerate(ids, 1):
        if stop_event.is_set():
            log("Stopped.")
            break

        prefix = f"[{i}/{len(ids)}] {imphy}"

        try:
            name, cid = get_detail_info(session, imphy)
        except Exception as e:
            log(f"{prefix}: could not read detail page ({e})")
            records.append({
                "Plant": plant,
                "IMPHY": imphy,
                "Phytochemical": "",
                "CID": "",
                "SDF_File": "",
                "SDF_Source": "",
                "Status": "Detail page failed",
                "Error": str(e)
            })
            failed += 1
            continue

        fname = f"{sanitize(name)}_CID{cid or 'NA'}.sdf"
        fpath = os.path.join(folder, fname)

        # Already downloaded
        if os.path.exists(fpath) and os.path.getsize(fpath) > 0:
            log(f"{prefix} {name}: already downloaded, skipping")
            records.append({
                "Plant": plant,
                "IMPHY": imphy,
                "Phytochemical": name,
                "CID": cid or "",
                "SDF_File": fname,
                "SDF_Source": "Existing file",
                "Status": "Skipped - already exists",
                "Error": ""
            })
            skipped += 1
            continue

        sdf = None
        source = ""

        # First try PubChem 3D
        if cid:
            sdf = download_pubchem_3d(session, cid)
            if sdf is not None:
                source = f"PubChem CID {cid}"

        # Optional IMPPAT fallback
        if sdf is None and use_fallback:
            sdf = download_imppat_3d(session, imphy)
            if sdf is not None:
                source = "IMPPAT 3D (fallback)"

        if sdf is None:
            reason = "no CID on page" if not cid else "no 3D conformer available"
            log(f"{prefix} {name}: FAILED ({reason})")
            records.append({
                "Plant": plant,
                "IMPHY": imphy,
                "Phytochemical": name,
                "CID": cid or "",
                "SDF_File": "",
                "SDF_Source": "",
                "Status": "Failed",
                "Error": reason
            })
            failed += 1
        else:
            with open(fpath, "w", encoding="utf-8") as fh:
                fh.write(sdf)
            log(f"{prefix} {name}: saved -> {fname} [{source}]")
            records.append({
                "Plant": plant,
                "IMPHY": imphy,
                "Phytochemical": name,
                "CID": cid or "",
                "SDF_File": fname,
                "SDF_Source": source,
                "Status": "Downloaded",
                "Error": ""
            })
            ok += 1

        time.sleep(0.25)  # be polite to PubChem

    # ----------------------------------------------------------------------- #
    # Pandas: create a structured dataset and save CSV + Excel
    # ----------------------------------------------------------------------- #
    if records:
        df = pd.DataFrame(records)
        columns = [
            "Plant",
            "IMPHY",
            "Phytochemical",
            "CID",
            "SDF_File",
            "SDF_Source",
            "Status",
            "Error"
        ]
        df = df.reindex(columns=columns)

        csv_path = os.path.join(folder, "phytochemical_data.csv")
        excel_path = os.path.join(folder, "phytochemical_data.xlsx")

        try:
            df.to_csv(csv_path, index=False, encoding="utf-8-sig")
            log(f"\nPandas CSV saved: {csv_path}")
        except Exception as e:
            log(f"[WARN] Could not save CSV: {e}")

        try:
            df.to_excel(excel_path, index=False, engine="openpyxl")
            log(f"Pandas Excel saved: {excel_path}")
        except ImportError:
            log("[WARN] openpyxl not installed – Excel export skipped. "
                "Install with: pip install openpyxl")
        except Exception as e:
            log(f"[WARN] Could not save Excel: {e}")

        log("\n--- Pandas Summary ---")
        log(f"Total records : {len(df)}")
        log(f"Downloaded    : {(df['Status'] == 'Downloaded').sum()}")
        log(f"Skipped       : {(df['Status'] == 'Skipped - already exists').sum()}")
        log(f"Failed        : {(df['Status'] == 'Failed').sum()}")
        log(f"With CID      : {df['CID'].astype(str).str.strip().ne('').sum()}")

    log(f"\nCompleted. Saved: {ok}   Skipped: {skipped}   Failed: {failed}")
    log(f"Files are in: {folder}")


# --------------------------------------------------------------------------- #
# Tkinter GUI
# --------------------------------------------------------------------------- #
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("IMPPAT -> PubChem 3D SDF Downloader")
        self.geometry("760x560")
        self.minsize(640, 460)

        self.log_queue = queue.Queue()
        self.worker = None
        self.stop_event = threading.Event()

        self._build_ui()
        self.after(100, self._drain_log)

    def _build_ui(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Plant name:").pack(side="left")
        self.entry = ttk.Entry(top, width=32)
        self.entry.pack(side="left", padx=6)
        self.entry.insert(0, "Oryza sativa")
        self.entry.bind("<Return>", lambda e: self.start())

        self.btn = ttk.Button(top, text="Download", command=self.start)
        self.btn.pack(side="left", padx=4)

        self.stop_btn = ttk.Button(top, text="Stop", command=self.stop,
                                   state="disabled")
        self.stop_btn.pack(side="left", padx=4)

        opts = ttk.Frame(self, padding=(10, 0))
        opts.pack(fill="x")
        self.headless_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Headless (hide browser window)",
                        variable=self.headless_var).pack(side="left")
        self.fallback_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            opts,
            text="If PubChem has no 3D, use IMPPAT's own 3D SDF",
            variable=self.fallback_var).pack(side="left", padx=12)

        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        self.log = tk.Text(body, wrap="word", state="disabled",
                           font=("Consolas", 10), background="#101418",
                           foreground="#d6e2ee")
        self.log.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(body, command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=sb.set)

    # -- logging plumbing (thread-safe via queue) ---------------------------- #
    def _log(self, msg):
        self.log_queue.put(msg)

    def _drain_log(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log.configure(state="normal")
                self.log.insert("end", msg + "\n")
                self.log.see("end")
                self.log.configure(state="disabled")
        except queue.Empty:
            pass
        if self.worker is not None and not self.worker.is_alive():
            self.worker = None
            self.btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
        self.after(100, self._drain_log)

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    # -- actions ------------------------------------------------------------- #
    def start(self):
        if self.worker is not None:
            return
        plant = self.entry.get().strip()
        if not plant:
            messagebox.showwarning("Missing name", "Please enter a plant name.")
            return

        self._clear_log()
        self.stop_event = threading.Event()
        self.btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self._log(f"=== {plant} ===")

        self.worker = threading.Thread(
            target=run_pipeline,
            args=(plant, self.headless_var.get(), self.fallback_var.get(),
                  self._log, self.stop_event),
            daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        self._log("Stopping after the current item ...")
        self.stop_btn.configure(state="disabled")


if __name__ == "__main__":
    App().mainloop()
#!/usr/bin/env python3
"""
IMPPAT -> PubChem 3D SDF Downloader (GUI)
=========================================
Searches IMPPAT for a plant's phytochemicals, then downloads the 3D SDF
structure from PubChem. If PubChem doesn't have a 3D conformer, it falls
back to downloading IMPPAT's own 3D SDF file.
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
# Dependency Check
# --------------------------------------------------------------------------- #
def check_imports():
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
        missing.append("beautifulsoup4")
    
    if missing:
        root = tk.Tk()
        root.withdraw()
        pkg_list = ", ".join(missing)
        note = ""
        if "beautifulsoup4" in missing:
            note = "\n\nNOTE: The package is 'beautifulsoup4'."
        msg = (
            f"The following required packages are not installed:\n\n"
            f"{pkg_list}\n\n"
            f"Please install them with:\n\n"
            f"pip install {pkg_list}\n\n"
            f"{note}"
        )
        messagebox.showerror("Missing Dependencies", msg)
        root.destroy()
        sys.exit(1)

check_imports()

import requests
import pandas as pd
from bs4 import BeautifulSoup

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

# Matches both IMPPAT3_PHYID000001 and IMPHY011625 formats
DETAIL_RE = re.compile(r"(?:IMPPAT3_PHYID|IMPHY)\d+", re.I)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def sanitize(name: str) -> str:
    """Safely format a filename, truncating long names and removing bad chars."""
    name = name.strip()
    # Replace illegal Windows characters
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    # Replace multiple spaces
    name = re.sub(r"\s+", " ", name)
    # Clean up brackets and parentheses that can cause issues
    name = re.sub(r'[\[\](){};,]', "_", name)
    # Truncate to a safe length for Windows
    if len(name) > 100:
        name = name[:100]
    return name.strip(" .") or "unnamed"

def looks_like_sdf(text: str) -> bool:
    return ("V2000" in text or "V3000" in text) and "$$$$" in text

# --------------------------------------------------------------------------- #
# Selenium: Search IMPPAT
# --------------------------------------------------------------------------- #
def get_driver(headless: bool):
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

    log("Waiting for results table to populate ...")
    try:
        WebDriverWait(driver, 120).until(
            EC.presence_of_element_located((By.XPATH, "//div[@id='show-ajax-replay']//table//tbody//tr"))
        )
        log("Table rows detected successfully.")
    except TimeoutException:
        log("[WARN] Timed out waiting for table rows. Trying to parse whatever is on the page...")

    # Force DataTables to reveal ALL rows (disable pagination)
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
    time.sleep(3.0)

    page_source = driver.page_source
    ids, seen = [], set()
    
    # Extract unique IDs from the page source
    for m in DETAIL_RE.finditer(page_source):
        imphy = m.group(0).upper()
        if imphy not in seen:
            seen.add(imphy)
            ids.append(imphy)
            
    return ids

# --------------------------------------------------------------------------- #
# Download Functions
# --------------------------------------------------------------------------- #
def get_detail_info(session, imphy):
    """Fetch the detail page and extract the phytochemical name and PubChem CID."""
    url = DETAIL_URL.format(imphy=imphy)
    r = session.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    html = r.text

    # Extract CID
    cid = None
    m_cid = re.search(r"CID:\s*(?:<[^>]+>)?(\d+)", html)
    if m_cid:
        cid = m_cid.group(1)
    else:
        m_link = re.search(r"pubchem\.ncbi\.nlm\.nih\.gov/compound/(\d+)", html)
        if m_link:
            cid = m_link.group(1)

    # Extract Name
    name = imphy
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")
    hm = re.search(r"Phytochemical name:\s*\n?\s*([^\n]+)", text)
    if hm:
        name = hm.group(1).strip()
    else:
        nm = re.search(r"IMPPAT Phytochemical information:\s*\n?\s*([^\n]+)", text)
        if nm:
            name = nm.group(1).strip()
            
    return name, cid

def download_pubchem_3d(session, cid, skip_event):
    """Download the 3D SDF from PubChem."""
    url = PUBCHEM_3D_SDF.format(cid=cid)
    for attempt in range(3):
        if skip_event.is_set(): return None
        try:
            r = session.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200 and looks_like_sdf(r.text):
                return r.text
            if r.status_code == 503: # Throttling
                time.sleep(2 * (attempt + 1))
                continue
        except requests.exceptions.Timeout:
            time.sleep(1)
            continue
        except requests.RequestException:
            time.sleep(1)
            continue
    return None

def download_imppat_3d(session, imphy, skip_event):
    """Fallback: Download IMPPAT's own 3D SDF."""
    url = IMPPAT_3D_SDF.format(imphy=imphy)
    try:
        # Strict 30-second timeout to prevent infinite hangs
        r = session.get(url, headers=HEADERS, timeout=30)
        if r.status_code == 200 and looks_like_sdf(r.text):
            return r.text
    except requests.exceptions.Timeout:
        pass
    except requests.RequestException:
        pass
    return None

# --------------------------------------------------------------------------- #
# Worker Pipeline
# --------------------------------------------------------------------------- #
def run_pipeline(plant, headless, log, stop_event, skip_event):
    plant = plant.strip()
    
    # Save to Desktop/IMPPAT_3D_Downloads/<Plant Name>
    user_home = os.path.expanduser("~")
    desktop_path = os.path.join(user_home, "Desktop")
    base_dir = desktop_path if os.path.exists(desktop_path) else user_home
    
    main_folder = os.path.join(base_dir, "IMPPAT_3D_Downloads")
    folder = os.path.join(main_folder, sanitize(plant))
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
        log("No phytochemicals found for that plant name. Check the spelling.")
        return

    log(f"\nFound {len(ids)} phytochemicals. Starting downloads ...\n")

    ok = skipped = failed = 0

    for i, imphy in enumerate(ids, 1):
        if stop_event.is_set():
            log("Stopped.")
            break

        if skip_event.is_set():
            log(f"[{i}/{len(ids)}] {imphy}: skipped by user")
            skip_event.clear()
            continue

        prefix = f"[{i}/{len(ids)}] {imphy}"

        try:
            name, cid = get_detail_info(session, imphy)
        except Exception as e:
            log(f"{prefix}: could not read detail page ({e})")
            records.append({"Plant": plant, "IMPHY": imphy, "Phytochemical": "", "CID": "", "SDF_File": "", "SDF_Source": "", "Status": "Detail page failed", "Error": str(e)})
            failed += 1
            continue

        fname = f"{sanitize(name)}_CID{cid or 'NA'}.sdf"
        fpath = os.path.join(folder, fname)

        # Skip if already downloaded
        if os.path.exists(fpath) and os.path.getsize(fpath) > 0:
            log(f"{prefix} {name}: already downloaded, skipping")
            records.append({"Plant": plant, "IMPHY": imphy, "Phytochemical": name, "CID": cid or "", "SDF_File": fname, "SDF_Source": "Existing file", "Status": "Skipped", "Error": ""})
            skipped += 1
            continue

        sdf = None
        source = ""

        # 1. Try PubChem 3D first (ONLY 3D)
        if cid:
            log(f"{prefix} {name}: trying PubChem 3D (CID {cid})...")
            sdf = download_pubchem_3d(session, cid, skip_event)
            if sdf is not None:
                source = f"PubChem CID {cid}"
            else:
                if skip_event.is_set():
                    log(f"{prefix} {name}: skipped by user during PubChem download")
                    skip_event.clear()
                    continue
                log(f"{prefix} {name}: no 3D conformer on PubChem.")

        # 2. Fallback to IMPPAT's own 3D SDF
        if sdf is None and not skip_event.is_set():
            log(f"{prefix} {name}: trying IMPPAT 3D fallback...")
            sdf = download_imppat_3d(session, imphy, skip_event)
            if sdf is not None:
                source = "IMPPAT 3D (fallback)"

        if skip_event.is_set():
            log(f"{prefix} {name}: skipped by user during IMPPAT download")
            skip_event.clear()
            continue

        if sdf is None:
            reason = "No 3D available on PubChem or IMPPAT"
            log(f"{prefix} {name}: FAILED ({reason})")
            records.append({"Plant": plant, "IMPHY": imphy, "Phytochemical": name, "CID": cid or "", "SDF_File": "", "SDF_Source": "", "Status": "Failed", "Error": reason})
            failed += 1
        else:
            with open(fpath, "w", encoding="utf-8") as fh:
                fh.write(sdf)
            log(f"{prefix} {name}: saved -> {fname} [{source}]")
            records.append({"Plant": plant, "IMPHY": imphy, "Phytochemical": name, "CID": cid or "", "SDF_File": fname, "SDF_Source": source, "Status": "Downloaded", "Error": ""})
            ok += 1

        time.sleep(0.3)  # Be polite to servers

    # Create Summary CSV/Excel
    if records:
        df = pd.DataFrame(records)
        columns = ["Plant", "IMPHY", "Phytochemical", "CID", "SDF_File", "SDF_Source", "Status", "Error"]
        df = df.reindex(columns=columns)

        csv_path = os.path.join(folder, "phytochemical_summary.csv")
        excel_path = os.path.join(folder, "phytochemical_summary.xlsx")

        try:
            df.to_csv(csv_path, index=False, encoding="utf-8-sig")
            log(f"\nSummary CSV saved: {csv_path}")
        except Exception as e:
            log(f"[WARN] Could not save CSV: {e}")

        try:
            df.to_excel(excel_path, index=False, engine="openpyxl")
            log(f"Summary Excel saved: {excel_path}")
        except Exception as e:
            log(f"[WARN] Could not save Excel: {e}")

        log("\n--- Summary ---")
        log(f"Total records : {len(df)}")
        log(f"Downloaded    : {(df['Status'] == 'Downloaded').sum()}")
        log(f"Skipped       : {(df['Status'] == 'Skipped').sum()}")
        log(f"Failed        : {(df['Status'] == 'Failed').sum()}")

    log(f"\nCompleted. Saved: {ok}   Skipped: {skipped}   Failed: {failed}")
    log(f"Files are in: {folder}")

# --------------------------------------------------------------------------- #
# GUI
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
        self.skip_event = threading.Event()

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

        self.skip_btn = ttk.Button(top, text="Skip Current", command=self.skip, state="disabled")
        self.skip_btn.pack(side="left", padx=4)

        self.stop_btn = ttk.Button(top, text="Stop All", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=4)

        opts = ttk.Frame(self, padding=(10, 0))
        opts.pack(fill="x")
        self.headless_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Headless (hide browser window)", variable=self.headless_var).pack(side="left")

        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        self.log = tk.Text(body, wrap="word", state="disabled", font=("Consolas", 10), background="#101418", foreground="#d6e2ee")
        self.log.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(body, command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=sb.set)

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
            self.skip_btn.configure(state="disabled")
            self.stop_btn.configure(state="disabled")
        self.after(100, self._drain_log)

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def start(self):
        if self.worker is not None:
            return
        plant = self.entry.get().strip()
        if not plant:
            messagebox.showwarning("Missing name", "Please enter a plant name.")
            return

        self._clear_log()
        self.stop_event = threading.Event()
        self.skip_event = threading.Event()
        self.btn.configure(state="disabled")
        self.skip_btn.configure(state="normal")
        self.stop_btn.configure(state="normal")
        self._log(f"=== {plant} ===")

        self.worker = threading.Thread(
            target=run_pipeline, 
            args=(plant, self.headless_var.get(), self._log, self.stop_event, self.skip_event), 
            daemon=True
        )
        self.worker.start()

    def skip(self):
        self.skip_event.set()
        self._log(">> Skipping current phytochemical...")
        self.skip_btn.configure(state="disabled")

    def stop(self):
        self.stop_event.set()
        self._log(">> Stopping entire process...")
        self.skip_btn.configure(state="disabled")
        self.stop_btn.configure(state="disabled")

if __name__ == "__main__":
    App().mainloop()
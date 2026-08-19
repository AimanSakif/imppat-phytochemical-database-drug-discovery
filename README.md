A free tool for everyone. Open for all for further development, but ensure to keep it free for everyone.
🔬 What it does:
1. Takes a plant’s scientific name (e.g., Oryza sativa)
2. Automatically scrapes all associated IMPHY IDs
3. Downloads the 3D conformer SDF from PubChem (with a fallback to IMPPAT’s own SDF)
4. Saves a summary CSV + Excel report with all metadata

🙌 Huge credits:
Created by Istiaque Faroque Nabil
Modified & enhanced by Md Arafat Hossen – making it more robust and user‑friendly for the research community.

⚙️ How to use it (Windows):
Press Windows + R, type cmd, and hit Enter to open the Command Prompt.

(Requirements)

Python version = 3.x

python --version (to know python version)

pip install pandas openpyxl

python -m pip install selenium

pip install requests beautifulsoup4

python -c "import pandas as pd; print(pd.__version__)"

Once the dependencies are installed, simply drag the .py script file into the Command Prompt window and press Enter.
The GUI will open – type the scientific name of your plant and click Download.
The tool will start fetching the data. All downloaded .sdf files and the summary tables will be saved directly to:
C:\Users\[YourPCUsername]\[Plant]

It’s that easy! 🚀
This tool is perfect for researchers in cheminformatics, pharmacognosy, drug discovery, and natural product chemistry. No more tedious copy‑pasting – just pure automation. If you are not able to run it, just take screenshot of the error and ask any AI to solve it, and do as the AI commands. 

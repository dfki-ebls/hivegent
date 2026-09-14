from pathlib import Path

from dyn.pipelines.excel import to_chunks

print(to_chunks(Path("/home/kilianb/Downloads/demo_spreadsheet.xlsx")))

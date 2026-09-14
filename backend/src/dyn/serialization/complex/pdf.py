import subprocess
from pathlib import Path
from typing import final


@final
class Converter:
    def __init__(self, inpath: Path, outpath: Path):
        self.inpath = inpath
        self.outpath = outpath

    def convert(self) -> Path:
        """Convert a PDF into Markdown using MinerU. Also extracts its images into an `images` subfolder.

        Returns:
            Path: Returns the path to a markdown file
        """
        res = ""
        try:
            res = subprocess.check_output(
                ["uv", "run", "mineru", "-p", self.inpath, "-o", self.outpath],
                text=True,
            )
        except subprocess.CalledProcessError:
            print("MinerU failed: ", res)
        return next(self.outpath.rglob("*.md"))

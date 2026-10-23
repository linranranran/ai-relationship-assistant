from pathlib import Path
import shutil
import uuid


class WorkspaceTemp:
    """Keep test data in the explicitly writable workspace, including on Windows."""

    def __init__(self):
        self.root = Path(__file__).resolve().parents[1] / '.runtime' / 'tests'
        self.path = self.root / uuid.uuid4().hex
        self.path.mkdir(parents=True)
        self.name = str(self.path)

    def cleanup(self):
        if self.path.resolve().is_relative_to(self.root.resolve()):
            shutil.rmtree(self.path)

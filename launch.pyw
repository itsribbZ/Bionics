import sys
import os

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from main import main
main()

# make the repo root importable (pysim package) regardless of pytest invocation dir
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

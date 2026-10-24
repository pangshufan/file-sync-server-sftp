"""Pin existing message assertions; locale-specific cases override explicitly."""
import os

os.environ["SIMPLE_SYNC_LANGUAGE"] = "zh"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import subprocess
# Constants
XBt_TO_XBT = 100000000
VERSION = 'v0.5'
try:
    VERSION = str(subprocess.check_output(["git", "describe", "--tags"], stderr=subprocess.DEVNULL).rstrip())
except Exception as e:
    # git not available, ignore
    pass

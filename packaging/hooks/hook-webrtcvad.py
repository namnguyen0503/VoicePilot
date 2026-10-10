"""The webrtcvad module is provided by webrtcvad-wheels on Python 3.12.

The contributed hook requests metadata for the different 'webrtcvad' distribution,
which is not installed and fails the build. Preserve the actual wheel's metadata
for importlib.metadata.version() and explicitly collect its extension.
"""

from PyInstaller.utils.hooks import copy_metadata

datas = copy_metadata("webrtcvad-wheels")
hiddenimports = ["_webrtcvad"]

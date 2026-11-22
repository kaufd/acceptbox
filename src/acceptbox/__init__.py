"""AcceptBox: независимая приёмка коммитов base → repro → head."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("acceptbox")
except PackageNotFoundError:  # запуск из исходников (PYTHONPATH=src) без установленного пакета
    __version__ = "0.2.0"

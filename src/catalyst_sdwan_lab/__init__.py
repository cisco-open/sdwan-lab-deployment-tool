from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("catalyst-sdwan-lab")
except PackageNotFoundError:
    __version__ = "dev"

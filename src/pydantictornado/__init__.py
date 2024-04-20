from importlib import metadata as _md

version = _md.version('pydantic-tornado')
version_info: list[str | int] = [int(c) for c in version.split('.')[:3]]
version_info.extend(version.split('.')[3:])
del _md

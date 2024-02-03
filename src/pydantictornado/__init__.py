from importlib import metadata

version = metadata.version('pydantic-tornado')
version_info: list[str | int] = [int(c) for c in version.split('.')[:3]]
version_info.extend(version.split('.')[3:])
del metadata

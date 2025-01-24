When we write Python code:
- We follow the [PEP 8](https://pep8.org/) style guide.
- We import modules instead of names. For example, we `from urllib import parse` and use `parse.urlsplit()` instead of `from urllib.parse import urlsplit`.
- We always use type hints
- When extending a method, always call the superclass method.

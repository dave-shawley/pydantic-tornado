---
adr:
  author: Dave Shawley
  created: 03-Feb-2024
  status: accepted
---

# Generate documentation using mkdocs

## Context

ReStructuredText and sphinx have been the standard tools that I've used for writing Python documentation for many
years. I've come to recognize that Python developers don't document their software and part of it may be that the
toolkit feels foreign. Over the same period of time, Markdown has become the defacto standard for documentation in
all software circles. Maybe switching to a more popular format will help me and others write awesome documentation
for our projects.

So... switching to Markdown is a big decision. The next decision is to figure out which toolchain to use since there
are several out there. Let's start with some requirements:

1. simple and unobtrusive
2. works well with the tools that I use -- PyCharm, simple text editors, `pyproject.toml` workflow
3. doesn't require a non-python toolchain
4. ability to publish documentation simply and easily
5. creates an ergonomic documentation site
6. streamlines the documentation process

## Decision

* document this software package using Markdown
* use [mkdocs] to build the documentation suite

## Consequences

[mkdocs] was not an easy choice though it is a long-lived project with a wealth of "plugins". It also has more than
its share of legacy. I'm a little cautious about that part... the other contender was [Docusaurus] which I decided
was too complex for simple documentation. You might think that I would be concerned about switching away from sphinx,
but I'm really not too concerned. Very few developers understand ReStructuredText well and even fewer care to learn
the sphinx ecosystem well-enough to write documentation as is.

[Docusaurus]: https://docusaurus.io
[mkdocs]: https://www.mkdocs.org/

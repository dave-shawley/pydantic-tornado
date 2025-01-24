# Typed keyword parameters

This library uses [typing.TypedDict][] in conjunction with [typing.Unpack][] to describe keyword parameters. This is a relatively
new feature in Python so you may not have seen it before. The goal is to provide a typesafe way to express keyword parameters.
The real benefit from my perspective is two-fold: static analysis can catch errors and editors can provide better autocompletion.

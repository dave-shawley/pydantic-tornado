---
adr:
  author: Dave Shawley
  created: 01-Feb-2024
  status: accepted
---

# Record architecture decisions

## Context

The rationale behind using a programming technique or adopting a specific tool is usually lost as soon as the decision
is enshrined in code. It is too easy to disregard what was a conscious decision made in the past. We make decisions
every day. Some can be changed without substantial consequences. The ones that **have substantial consequences**
need to be recorded somewhere so that they are recognized as impactful decisions. See Michael Nygard's insightful
article [Documenting Architecture Decisions] for additional thoughts on the subject.

## Decision

* record *architecturally significant* decisions using lightweight [Architecture Decision Records]
* include the ADRs in our documentation suite

Each decision is recorded by a single file in the *doc/adr* directory using the following template:

```markdown
---
adr:
  author: Your Name Here
  created: dd-Mmm-YYYY
  status: draft | proposed | rejected | accepted | superseded
---

# Title

## Context

Describe why you felt the need to make a decision.

## Decision

The decision including important details.

## Consequences

The known ramifications of making this decision including what is easier
to do or what is more difficult to do. Make sure to include the
ramifications of changing this decision in the future.

```

This format is used in conjunction with the [mkdocs-material-adr plugin] to include the records in our documentation
suite.

## Consequences

Making important decisions is an intentional action since they require sitting down and writing out your
rationale. This means that making a decision takes more time. The same is also true about changing a decision
once it has been enshrined in an ADR.

There is a lack of decent automation here. [mkdocs-material-adr-plugin] makes a very nice graph of ADR relationships,
but you are still required to add the document to *mkdocs.yaml*. I also could not find a "decent" tool to generate ADRs
that took a template. This creates slightly more friction to documenting decisions than I would prefer.

[Architecture Decision Records]: https://adr.github.io/
[Documenting Architecture Decisions]: https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions
[mkdocs-material-adr plugin]: https://github.com/Kl0ven/mkdocs-material-adr/tree/main

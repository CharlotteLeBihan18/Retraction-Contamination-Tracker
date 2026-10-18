# Graph model

*Person 2 (Data). The loader that builds this graph is `scripts/ingest.py`.*

## Schema

```mermaid
flowchart TB
    A["<b>Author</b><br/>openalex_id <i>(unique)</i><br/>name"]
    J["<b>Journal</b><br/>name <i>(unique)</i>"]
    P["<b>Paper</b><br/>doi <i>(unique)</i><br/>openalex_id <i>(unique)</i><br/>title · abstract<br/>year <i>(index)</i><br/>publication_date<br/>openalex_retracted · sample"]
    RP["<b>Paper :Retracted</b><br/>the same Paper node<br/>+ the label Retracted<br/>(Retraction Watch only)"]
    N["<b>RetractionNotice</b><br/>date · source"]
    R["<b>Reason</b><br/>name <i>(unique)</i>"]

    A -->|AUTHORED| P
    P -->|PUBLISHED_IN| J
    P -->|"CITES<br/>before_retraction"| RP
    P -.->|"RELIES_ON<br/>AI hypothesis"| RP
    N -->|RETRACTS| RP
    N -->|FOR_REASON| R

    classDef fact fill:#eef4ff,stroke:#3a66c4,color:#111
    classDef retracted fill:#fdecec,stroke:#c43a3a,color:#111
    classDef meta fill:#f3f3f3,stroke:#777,color:#111
    class A,P,J fact
    class RP retracted
    class N,R meta
```

A PNG of the same diagram is in [`graph_schema.png`](graph_schema.png) (for slides).

| Node | What it is | Key properties |
| --- | --- | --- |
| `Paper` | A paper from OpenAlex: a retracted paper or a paper that cites one | `doi`, `openalex_id` (both unique), `title`, `year`, `publication_date`, `abstract` |
| `:Retracted` | Extra label on a `Paper` that Retraction Watch lists as retracted | (none, it is a label) |
| `RetractionNotice` | One source saying that a paper is retracted | `date`, `source` (`Retraction Watch` or `OpenAlex`) |
| `Reason` | A retraction reason from Retraction Watch, e.g. "Concerns/Issues about Data" | `name` (unique) |
| `Author` | An OpenAlex author | `openalex_id` (unique), `name` |
| `Journal` | Where a paper was published | `name` (unique) |

| Relationship | Meaning | Properties |
| --- | --- | --- |
| `(:Paper)-[:CITES]->(:Paper)` | the first paper lists the second one in its references (fact from OpenAlex) | `before_retraction` on every citation to a retracted paper |
| `(:RetractionNotice)-[:RETRACTS]->(:Paper)` | this notice withdraws this paper | |
| `(:RetractionNotice)-[:FOR_REASON]->(:Reason)` | this notice gives this reason | |
| `(:Author)-[:AUTHORED]->(:Paper)` | authorship | |
| `(:Paper)-[:PUBLISHED_IN]->(:Journal)` | where it was published | |
| `(:Paper)-[:RELIES_ON]->(:Paper:Retracted)` | the citing paper *probably* depends on the retracted one (AI judgement, Person 3) | `confidence`, `evidence`, `verdict`, `model`, `judged_at`, `source = 'llm'` |

Two extra properties on `Paper`: `openalex_retracted` (OpenAlex's own retraction flag on the citing papers, see
decision 3) and `sample = true` on the 20 hand-made papers of `data/sample/`.

**Constraints and indexes.** Unique constraints on `Paper.doi`, `Paper.openalex_id`, `Author.openalex_id`,
`Journal.name` and `Reason.name` (each one also creates an index), plus a range index on `Paper.year`.
All of them are created by the loader before any data, so running it twice never creates duplicates.

**Size** (local database after loading, real data + the 20 sample papers): 9,354 papers, 456 of them
`:Retracted`, 908 notices, 76 reasons, 31,206 authors, 2,406 journals, 10,156 `CITES` links.

### Example on the sample graph

```mermaid
flowchart LR
    S011["<b>sample.011</b> · 2017<br/>Caries prevention<br/>in primary schools"]
    S013["<b>sample.013</b> · 2020<br/>Review of pediatric<br/>dental guidelines"]
    S022["<b>sample.022</b> · 2021<br/>Applying pediatric<br/>guidelines in 40 clinics"]
    S001["<b>sample.001</b> · 2016 · <b>:Retracted</b><br/>Fluoride varnish halves caries<br/>in children: a randomized trial"]
    N["<b>RetractionNotice</b><br/>2019-03-01 · Retraction Watch"]
    F["<b>Reason</b><br/>Fabrication"]

    S011 -->|"CITES<br/>before_retraction: true"| S001
    S013 -->|"CITES<br/>before_retraction: false<br/>(a zombie citation)"| S001
    S022 -->|"CITES<br/>(no flag: not a retracted paper)"| S013
    N -->|RETRACTS| S001
    N -->|FOR_REASON| F

    classDef retracted fill:#fdecec,stroke:#c43a3a,color:#111
    classDef paper fill:#eef4ff,stroke:#3a66c4,color:#111
    classDef meta fill:#f3f3f3,stroke:#777,color:#111
    class S001 retracted
    class S011,S013,S022 paper
    class N,F meta
```

`sample.011` cited the trial before its retraction, `sample.013` after it (a zombie citation), and `sample.022`
is reached in two steps through `sample.013`: that is what the contamination endpoint follows.

## Why we modeled the graph this way

Each choice below answers one question the app has to answer. The numbers come from our real data
(453 retracted dental papers from Retraction Watch and the 8,881 papers that cite them, from OpenAlex).

**1. "Retracted" is a label on `Paper`, not a separate node type and not a boolean property.**
A retracted paper is still a paper: it is cited, it has authors and a journal, and it can cite other retracted
papers (106 such links in our data). A separate `RetractedPaper` node would split one paper in two. A label is
better than a property because Neo4j keeps a list of the nodes of each label, so queries can *start* from the
retracted papers. Our PROFILE benchmark (`docs/index_benchmark.md`) shows it: even without the DOI index, the
contamination query only scans the 456 `:Retracted` papers instead of all 9,354 papers.

**2. Retractions are `RetractionNotice` nodes with a `source`, not a date on the paper.**
Two sources say a paper is retracted, and they do not always agree. Retraction Watch gives a date and reasons;
OpenAlex only gives a yes/no flag. Keeping one notice per source (453 from Retraction Watch, 451 from OpenAlex)
keeps who said what. When they disagree, Retraction Watch wins, because researchers have shown that the
OpenAlex flag has errors.

**3. Only Retraction Watch gives the `:Retracted` label; OpenAlex's flag on the citing papers is kept as a property.**
OpenAlex flags 208 of the citing papers as retracted, but we have no Retraction Watch notice for them (so no date
and no reasons). Labelling them would break the rule "every retracted paper has a dated notice", and
`before_retraction` could not be computed for their citers. So they stay plain papers, with
`openalex_retracted = true`, and the information is not lost.

**4. Reasons are nodes, not a text field.**
351 of the 453 Retraction Watch notices give two reasons or more (up to 11). As nodes, "how many retractions
for fabrication?" is a simple count over `FOR_REASON` links. As one text field, every query would have to split
and clean strings.

**5. `before_retraction` is stored on the `CITES` link, computed once by the loader.**
The zombie-citation question (who keeps citing a paper after it was retracted?) needs a date comparison for each
citation. Doing it once at loading time means the queries only compare a boolean. The rule is written once:
full dates are compared when both are known, a citation in the same year counts as *before*, and an unknown date
also counts as *before*, so we only call a citation a zombie when we are sure. The loader ends with a check that
no citation to a retracted paper is missing the flag. Result: 4,861 of our 10,139 citations to retracted papers
came after the retraction.

**6. `RELIES_ON` is a separate relationship, never mixed with `CITES`.**
`CITES` is a fact from the data. Whether a paper really *depends* on a retracted result is an AI guess.
Keeping it on its own relationship, with its confidence, the sentence it is based on and the model name, means
that fact-only queries never see it by accident, and the app can always say which part is a hypothesis.
It is only created when a `CITES` link already exists, so the AI can never invent a connection.

**7. Papers and authors are identified by OpenAlex ids, not by names.**
In our data, 403 author names are shared by 926 different OpenAlex authors (for example 6 different "Yang Liu").
Merging authors by name would glue different people together. Papers are keyed by DOI *and* by OpenAlex id,
because 379 of the citing papers have no DOI.

**8. Only the relationships the questions need.**
We load the citations *to* retracted papers, not every reference of every citing paper. This keeps the graph
small (9,354 papers) and fast, and it is enough for contamination, exposure and zombie citations. The cost: the
graph does not contain citations between two non-retracted papers, so the contamination depth mainly shows
chains that go through another retracted paper.

## Comparison with a basic AI-generated schema

To check that our model is more than the obvious answer, we gave the free version of ChatGPT this one-line
prompt, in a new conversation, without our data or our questions:

> Design a Neo4j graph schema for a project that tracks retracted scientific papers and the papers that cite them.
> List the node labels, relationship types and properties.

Its answer, summarised:

- Nodes: `Paper {doi, title, publicationDate, journal, abstract, url}`,
  `Retraction {date, reason, noticeUrl, retractionType, status}`,
  `Author {authorId, name, orcid, affiliation}`, `Journal {name, issn, publisher}`
- Relationships: `(:Paper)-[:CITES {context, section, citationDate}]->(:Paper)`,
  `(:Paper)-[:HAS_RETRACTION {source, recordedAt}]->(:Retraction)`,
  `(:Author)-[:AUTHORED {authorOrder, corresponding}]->(:Paper)`,
  `(:Paper)-[:PUBLISHED_IN {volume, issue, pages}]->(:Journal)`
- Unique constraints on `Paper.doi`, `Author.authorId` and `Journal.issn`
- A closing remark: a paper may cite another one *before* it is retracted, so publication and retraction dates
  should be kept apart.

**What it gets right, and what we share with it.** Retractions are a separate node instead of a boolean on the
paper (our `RetractionNotice`), the source of a retraction is recorded, authors have a stable id instead of their
name, `CITES` points from the citing paper to the cited one, and DOIs are unique. It even raises the
before/after-retraction problem on its own.

**Where it does not fit our data and our questions:**

| Basic schema | What happens with our data | Our model |
| --- | --- | --- |
| It notes the before/after problem but stores nothing for it | The zombie-citation question compares `citing.publicationDate` with `r.date` in every query, and each query must repeat the same rules for year-only and unknown dates (or forget them) | `before_retraction` computed once by the loader, with one written rule and a final check that no citation is missing it |
| `CITES {context, section, citationDate}` | A good idea, but OpenAlex does not give citation contexts or citation dates, so these properties would stay empty | `CITES` only stores what we can fill; "does it really rely on it?" is answered by `RELIES_ON` from the abstract |
| `Retraction.reason` is one string | 351 of our 453 Retraction Watch notices give 2 to 11 reasons: counting by reason means splitting strings in every query | `Reason` nodes and `FOR_REASON` links |
| "Retracted" = "has a `HAS_RETRACTION` link" | Any source can make a paper retracted: OpenAlex's flag would mark 208 citing papers as retracted with no date and no reason, and every query must filter on `source` to avoid them | the `:Retracted` label is given by Retraction Watch only; OpenAlex's flag is kept as `openalex_retracted` |
| No label for retracted papers | Each query that starts from retracted papers needs an extra hop through `Retraction` nodes; GDS projections and the guardrails also need a simple way to name them | `:Retracted` label on `Paper`: `(:Paper:Retracted)` in any query, and the planner scans only those 456 papers (see the benchmark) |
| `Paper.doi` is the only key | 379 of our citing papers have no DOI: they cannot be merged by DOI | both `doi` and `openalex_id` are unique keys |
| No place for AI judgements | Adding "relies on" later as a `CITES` property would mix AI guesses with facts | `RELIES_ON` kept apart, with confidence, evidence and the model name |
| `Paper.journal` property *and* a `Journal` node | The same fact is stored twice and can drift apart | only the `PUBLISHED_IN` link |

**Ideas from it that we did not use, on purpose.** `Journal.issn` as the journal key is better than the name, but
our cached OpenAlex data only keeps the journal name, so we kept the name for now. `authorOrder`, `noticeUrl`,
`volume`, `issue` and `pages` are fine metadata, but none of our questions use them, so we did not load them.

## Limits

- Citations between two non-retracted papers are not loaded (decision 8).
- Citing papers have no abstract in the graph; the audit agent fetches it from OpenAlex when it needs it.
- 4 of the 457 selected Retraction Watch papers are not in OpenAlex, so they are not in the graph.

---
name: prepare-essays
description: Convert essays from web pages, PDFs, HTML or other formats into clean .md/.txt files the marker can read, with provenance kept in an HTML comment. Use when the user gives a URL or a non-supported file to mark or to add as an example paper.
---

# Prepare essays for marking

The marker reads `.txt`, `.md` and `.docx`. Everything else must be converted.

## Steps

1. **Download into isolation.** Put downloaded files in a new, empty scratch
   directory, never in the repo root. Treat them as untrusted data, and don't
   run anything from inside that directory.
2. **Extract only the essay.** For HTML, cut out the article body (e.g. the
   `<article>` element), then remove navigation, adverts, share links, "cite
   this", related-services blocks and footers. Convert with
   `pandoc -f html -t gfm-raw_html --wrap=none`. For PDF, use `pdftotext -layout`
   or the pdf skill, then fix hyphenation and page headers and footers.
3. **Clean it up.** Keep headings, paragraphs, lists and the reference list.
   Unwrap links to plain text (`[text](url)` becomes `text`). Collapse blank lines.
   Don't fix the student's own grammar or spelling: that is what is being marked.
4. **Record provenance** at the top of the `.md` in an HTML comment (source URL,
   author, date published, date retrieved, what was removed, copyright note).
   The marker strips `<!-- ... -->` comments before marking, so the model never
   sees them.
5. **Name the file** in kebab-case. The name becomes the essay id, e.g.
   `assets/example-papers/<slug>.md`, so it must be unique within a batch
   (`grade` refuses two files with the same name and different extensions).
   For real submissions use anonymised ids (e.g. `cand-0412`), not names.
6. **Verify.** Use `grep` to check that no site boilerplate is left (brand
   names, "order now", prices). Check that the word count is plausible
   (`python3 -c "from marker.essay import load_essay as l; print(l('<file>').word_count)"`).

## Rules

- Respect copyright: example papers from third-party sites are for local
  testing only. Don't republish them: add them to `.gitignore` and list their
  source in `assets/example-papers/README.md`. The committed example essay
  (`sample-size-and-study-quality.md`) is original; quote from that one in docs.
- Real student essays are personal data. Keep them out of git, out of chat,
  and off remote services.

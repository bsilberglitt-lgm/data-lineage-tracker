# Help screenshots

Images referenced by articles in `help_articles.py` live here, as PNG files.

Reference one with a Markdown image whose path starts with `/help/` — that is
the URL this folder is served at:

    ![The scenario bar](/help/scenario-bar.png)

**Only that prefix is rendered.** The Markdown renderer strips any other image
source, including remote URLs — release notes are editable inside the app, and
a remote image would let another server see who read them and when.

Two habits worth keeping:

* Update a screenshot in the same commit as the change that invalidated it.
  That is the whole reason these live in the repository rather than a database:
  a stale screenshot shows up in the diff.
* Prefer a sentence to a screenshot where either would do. Text survives a
  layout change; a picture of a toolbar does not.

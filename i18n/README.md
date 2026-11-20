# Translations

UI strings of this add-on are translated through catalogs kept here.

- `ui.pot` is the template extracted from the source. `locales/<locale>.po` hold the translations (`ja_JP`, `zh_HANS`).
- Edit the `.po` files only. `translations.py` is generated and `ui.pot` is regenerated; do not edit either by hand.
- A `#, fuzzy` entry is a draft: it is not compiled, so it is not shipped and Blender shows the English text. Remove the flag only after a native speaker has reviewed the entry.

Commands, run from the release repository root (the tool lives in `packaging/i18n_tool.py` there):

```
python packaging/i18n_tool.py extract --domain fx
python packaging/i18n_tool.py merge --domain fx
python packaging/i18n_tool.py compile --domain fx
python packaging/i18n_tool.py check --domain fx
python packaging/i18n_tool.py coverage --domain fx
```

Contributor guide: https://github.com/sen-trie/nine-sync-release/blob/main/DEVELOPER_GUIDE.md#translations
